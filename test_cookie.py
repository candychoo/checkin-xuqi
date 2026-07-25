#!/usr/bin/env python3
"""
快速测试 ACL_COOKIES 是否有效 - 详细版
关键修复:
1. 直接访问 aclclouds.com (跳过 dash. 重定向)
2. 自动把 aclclouds_session 改名为 __Host-aclclouds_session
3. 同时用 Cookie header + cookie jar 双保险
"""
import os
import requests

COOKIE = os.environ.get("ACL_COOKIES", "").strip()
print(f"Cookie 长度: {len(COOKIE)}")
print(f"Cookie 前50字符: {COOKIE[:50]}...")

if not COOKIE:
    print("❌ ACL_COOKIES 为空！")
    exit(1)

# 解析 cookie 字符串成 dict
parsed = {}
for kv in COOKIE.split(";"):
    kv = kv.strip()
    if "=" in kv:
        k, v = kv.split("=", 1)
        parsed[k.strip()] = v.strip()

# 检查关键 Cookie
has_xsrf = "XSRF-TOKEN" in parsed
has_session_legacy = "aclclouds_session" in parsed
has_session_host = "__Host-aclclouds_session" in parsed
print(f"\n包含 XSRF-TOKEN: {has_xsrf}")
print(f"包含 aclclouds_session (旧名): {has_session_legacy}")
print(f"包含 __Host-aclclouds_session (新名): {has_session_host}")

# 关键: 服务器现在用 __Host-aclclouds_session, 自动改名
if has_session_legacy and not has_session_host:
    parsed["__Host-aclclouds_session"] = parsed.pop("aclclouds_session")
    print("🔄 已将 aclclouds_session 重命名为 __Host-aclclouds_session (服务器要求)")
    has_session_host = True

if not has_xsrf or not has_session_host:
    print("⚠️ 警告: Cookie 可能不完整")
    print(f"完整 Cookie: {COOKIE}")
    exit(1)

# 关键: 直接用 aclclouds.com (跳过 dash. 重定向)
BASE_URL = "https://aclclouds.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

s = requests.Session()
s.headers.update({
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/projects",
})

# 双保险 1: 设置 Cookie header
cookie_header = "; ".join(f"{k}={v}" for k, v in parsed.items())
s.headers["Cookie"] = cookie_header

# 双保险 2: 设置到 cookie jar 的多个域
for d in ["aclclouds.com", ".aclclouds.com", "dash.aclclouds.com"]:
    for k, v in parsed.items():
        s.cookies.set(k, v, domain=d, path="/")

# 打印实际设置的 Cookie (去重)
print(f"\nSession 中的 Cookie (去重):")
seen = set()
for cookie in s.cookies:
    if cookie.name in seen:
        continue
    seen.add(cookie.name)
    print(f"  {cookie.name} = {cookie.value[:30]}...")

# 测试 /api/client (直接访问 aclclouds.com, 不经过 dash. 重定向)
r = s.get(f"{BASE_URL}/api/client", timeout=15, allow_redirects=True)
print(f"\n=== GET {BASE_URL}/api/client ===")
print(f"HTTP 状态码: {r.status_code}")
print(f"最终 URL: {r.url}")
if r.history:
    print(f"重定向链路:")
    for h in r.history:
        print(f"  {h.status_code} → {h.url}")

if r.status_code == 200:
    import json
    try:
        data = r.json()
        servers = data.get("data", []) if isinstance(data, dict) else []
        print(f"✅ 成功！服务器数量: {len(servers)}")
        for srv in servers:
            attrs = srv.get("attributes", {}) if isinstance(srv, dict) else {}
            name = attrs.get("name", "unknown")
            print(f"  - {name}")
    except:
        print(f"响应: {r.text[:200]}")
else:
    print(f"❌ 失败: {r.text[:300]}")

#!/usr/bin/env python3
"""
快速测试 ACL_COOKIES 是否有效 - 详细版
"""
import os
import requests

COOKIE = os.environ.get("ACL_COOKIES", "").strip()
print(f"Cookie 长度: {len(COOKIE)}")
print(f"Cookie 前50字符: {COOKIE[:50]}...")

if not COOKIE:
    print("❌ ACL_COOKIES 为空！")
    exit(1)

# 检查关键 Cookie 是否存在
has_xsrf = "XSRF-TOKEN" in COOKIE
has_session = "aclclouds_session" in COOKIE
print(f"包含 XSRF-TOKEN: {has_xsrf}")
print(f"包含 aclclouds_session: {has_session}")

if not has_xsrf or not has_session:
    print("⚠️ 警告: Cookie 可能不完整，请重新从浏览器复制完整 Cookie")
    print(f"完整 Cookie: {COOKIE}")
    exit(1)

# 用和 renew.py 完全相同的方式构建 session
BASE_URL = "https://dash.aclclouds.com"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

s = requests.Session()
s.headers.update({
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/projects",
})

for kv in COOKIE.split(";"):
    kv = kv.strip()
    if "=" in kv:
        k, v = kv.split("=", 1)
        s.cookies.set(k.strip(), v.strip(), domain="dash.aclclouds.com", path="/")

# 打印实际设置的 Cookie
print(f"\nSession 中的 Cookie:")
for cookie in s.cookies:
    print(f"  {cookie.name} = {cookie.value[:20]}...")

# 测试 /api/client
r = s.get(f"{BASE_URL}/api/client", timeout=15)
print(f"\n=== GET /api/client ===")
print(f"HTTP 状态码: {r.status_code}")

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

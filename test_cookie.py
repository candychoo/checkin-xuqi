#!/usr/bin/env python3
"""
快速测试 ACL_COOKIES 是否有效
运行: python test_cookie.py
"""
import os
import requests

COOKIE = os.environ.get("ACL_COOKIES", "").strip()
print(f"Cookie 长度: {len(COOKIE)}")
print(f"Cookie 前50字符: {COOKIE[:50]}...")

if not COOKIE:
    print("❌ ACL_COOKIES 为空！请检查 GitHub Secrets 配置")
    exit(1)

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
})
for kv in COOKIE.split(";"):
    kv = kv.strip()
    if "=" in kv:
        k, v = kv.split("=", 1)
        s.cookies.set(k.strip(), v.strip(), domain="dash.aclclouds.com", path="/")

r = s.get("https://dash.aclclouds.com/api/client", timeout=15)
print(f"HTTP 状态码: {r.status_code}")
print(f"响应内容: {r.text[:500]}")

if r.status_code == 200:
    print("✅ Cookie 有效！")
else:
    print("❌ Cookie 无效或已过期")

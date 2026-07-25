# -*- coding: utf-8 -*-
"""
配置解析 (向后兼容模块)
注意: renew.py 已自行解析环境变量, 本模块仅供其他脚本 import 使用
"""
import os
import sys

# 站点 URL
RENEW_URL = os.environ.get("GAME4FREE_RENEW_URL", "").strip()
COOKIE = os.environ.get("GAME4FREE_COOKIE", "").strip()

# 多账号
ACCOUNTS = []
for line in os.environ.get("GAME4FREE_ACCOUNTS", "").split("\n"):
    line = line.strip()
    if not line:
        continue
    parts = line.split("|||")
    if len(parts) >= 3:
        ACCOUNTS.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

# TG 通知 (修复: 原代码 token 和 chat_id 映射反了)
TG_BOT = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT_ID", "")

MAX_TRIES = 3
THRESHOLD = 45 * 3600
MAX_ROUNDS = 10

# 验证 TG 配置格式
if TG_BOT and TG_CHAT:
    if not TG_BOT.isdigit():
        print("警告: TG_BOT_TOKEN 格式错误（应以数字开头）", file=sys.stderr)
    if not TG_CHAT.lstrip("-").isdigit():
        print("警告: TG_CHAT_ID 格式错误（应为数字，可为负数）", file=sys.stderr)

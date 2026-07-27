#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Host2Play 自动续期脚本（SeleniumBase UC + Hysteria2 代理版）
==========================================================
- 使用 SeleniumBase UC 模式（反检测更强）
- Sing-box Hysteria2 住宅 IP 代理（60.91.157.48）
- CF Turnstile CDP 自动完成验证
- 统一 TG 通知格式
"""

import os
import sys
import json
import time
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from pathlib import Path

try:
    from seleniumbase import ConfigGen, SeleniumBase
    has_sb = True
except ImportError:
    has_sb = False

try:
    import requests
    has_requests = True
except ImportError:
    has_requests = False


# ==========================================================
# 配置
# ==========================================================
RENEW_URL = os.getenv("H2P_RENEW_URL", "")
COOKIE_STR = os.getenv("H2P_COOKIE", "")
HYP_PROXY = os.getenv("H2P_HYSTERIA2_PROXY", "")  # Hysteria2 代理 URL
WARP_PROXY = os.getenv("H2P_WARP_PROXY", "")  # 备用 WARP
RENEW_THRESHOLD_SECONDS = 25 * 3600  # 超过 25h 则跳过
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TZ_CN = timezone(timedelta(hours=8))

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ==========================================================
# Telegram 通知（统一格式）
# ==========================================================
def tg_send(msg: str, title: str = "Host2Play"):
    """发送统一的 Telegram 通知"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    
    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    emoji_start = "🎮"
    
    formatted = f"{emoji_start} <b>{title}</b>\n⏰ {now_cn}\n\n{msg}"
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": formatted,
                "parse_mode": "HTML",
                "disable_notification": True,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"⚠️ TG 发送失败: {e}")


# ==========================================================
# Hysteria2 代理设置（如果使用 sing-box）
# ==========================================================
def setup_hproxy(proxy_url: Optional[str]) -> Optional[str]:
    """配置 Hysteria2 代理，返回本地 SOCKS5 地址或 None"""
    if not proxy_url:
        return None
    
    # 如果已有 sing-box 运行在 127.0.0.1:10800，直接返回
    if proxy_url.startswith("socks5://"):
        print(f"✅ 直接使用代理: {proxy_url}")
        return proxy_url
    
    # 如果是 hysteria2:// 格式，尝试用 sing-box 启动
    print(f"⚠️ 检测到 Hysteria2 代理 URL，需要安装 sing-box...")
    print("提示: 先手动设置好 sing-box，然后设置 H2P_WARP_PROXY=socks5://127.0.0.1:10800")
    return None


# ==========================================================
# SeleniumBase UC 页面创建
# ==========================================================
def create_uc_page(proxy_addr: Optional[str] = None):
    """创建 UC 模式的 SeleniumBase 页面"""
    if not has_sb:
        raise ImportError("请先安装: pip install seleniumbase")
    
    gen = ConfigGen()
    gen.uc(False)  # UC 禁用
    gen.no_sandbox()
    gen.disable_dev_shm_usage()
    gen.disable_gpu()
    gen.disable_blink_features("AutomationControlled")
    gen.set_user_agent(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
    
    if proxy_addr:
        gen.proxy(proxy_addr)
    
    page = SeleniumBase(base_config=gen)
    page.set_default_timeout(180)
    return page


# ==========================================================
# 视频广告处理
# ==========================================================
def handle_ad_video(page: SeleniumBase) -> bool:
    """处理视频广告，返回 True 表示成功跳过/播放完成"""
    print("⏳ 等待广告播放器出现...")
    
    # 寻找跳过按钮
    found_skip = False
    for _ in range(30):  # 最多 30 秒
        try:
            skip_btn = page.find_element(
                "css:button:contains(\"Skip\"), xpath://button[contains(text(),\"Skip\")]", 
                timeout=2
            )
            print("✅ 找到跳过按钮!")
            skip_btn.click()
            time.sleep(2)
            return True
        except:
            pass
        
        # 检查是否播放完成
        try:
            ended = page.execute_script("""
                var vids = document.querySelectorAll('video');
                for(var v of vids) { if(v.ended) return true; }
                return false;
            """)
            if ended:
                print("✅ 视频播放完成")
                return True
        except:
            pass
        
        time.sleep(1)
    
    print("⚠️ 广告超时，继续执行")
    return True


# ==========================================================
# Cookie 注入
# ==========================================================
def inject_cookies(page: SeleniumBase, cookie_str: str):
    """注入 Cookie"""
    if not cookie_str:
        return
    print("🔐 注入 Cookie...")
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            try:
                page.add_cookie(k.strip(), v.strip())
            except Exception as e:
                print(f"⚠️ Cookie 注入失败 {k}: {e}")


# ==========================================================
# 获取服务器剩余时间
# ==========================================================
def get_expire_info(page: SeleniumBase) -> tuple:
    """返回 (server_id, expires_text, seconds_remaining)"""
    sid = "Unknown"
    exp_txt = "Unknown"
    secs = -1
    
    for _ in range(5):
        try:
            text =Network error: Stream decode error: error decoding response body

Please resend your message to try again.

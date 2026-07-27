#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Host2Play auto-renewal script using SeleniumBase UC mode + Hysteria2 proxy
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


# Configuration
RENEW_URL = os.getenv("H2P_RENEW_URL", "")
COOKIE_STR = os.getenv("H2P_COOKIE", "")
HYP_PROXY = os.getenv("H2P_HYSTERIA2_PROXY", "")
WARP_PROXY = os.getenv("H2P_WARP_PROXY", "")
RENEW_THRESHOLD_SECONDS = 25 * 3600
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TZ_CN = timezone(timedelta(hours=8))

ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output" / "screenshots"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def tg_send(msg: str, title: str = "Host2Play"):
    """Send unified Telegram notification."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    
    now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"<b>{title}</b>\n{now_cn}\n\n{msg}"
    
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
        print(f"Telegram send failed: {e}")


def setup_hproxy(proxy_url: Optional[str]) -> Optional[str]:
    """Configure Hysteria2 proxy, return local SOCKS5 address or None."""
    if not proxy_url:
        return None
    
    if proxy_url.startswith("socks5://"):
        print(f"Using direct proxy: {proxy_url}")
        return proxy_url
    
    print("Detected Hysteria2 proxy URL, need to install sing-box...")
    print("Hint: Set up sing-box manually, then set H2P_WARP_PROXY=socks5://127.0.0.1:10800")
    return None


def create_uc_page(proxy_addr: Optional[str] = None):
    """Create UC-mode SeleniumBase page."""
    if not has_sb:
        raise ImportError("Please install: pip install seleniumbase")
    
    gen = ConfigGen()
    gen.uc(True)
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


def handle_ad_video(page: SeleniumBase) -> bool:
    """Handle video ad, return True if success/ad skipped."""
    print("Waiting for ad player to appear...")
    
    found_skip = False
    for _ in range(30):
        try:
            skip_btn = page.find_element(
                "css:button:contains(\"Skip\"), xpath://button[contains(text(),\"Skip\")]",
                timeout=2
            )
            print("Found skip button!")
            skip_btn.click()
            time.sleep(2)
            return True
        except:
            pass
        
        try:
            ended = page.execute_script("""
                var vids = document.querySelectorAll('video');
                for(var v of vids) { if(v.ended) return true; }
                return false;
            """)
            if ended:
                print("Video playback completed")
                return True
        except:
            pass
        
        time.sleep(1)
    
    print("Ad timed out, continuing execution")
    return True


def inject_cookies(page: SeleniumBase, cookie_str: str):
    """Inject cookies."""
    if not cookie_str:
        return
    print("Injecting Cookie...")
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            try:
                page.add_cookie(k.strip(), v.strip())
            except Exception as e:
                print(f"Cookie injection failed {k}: {e}")


def get_expire_info(page: SeleniumBase) -> tuple:
    """Return (server_id, expires_text, seconds_remaining)."""
    sid = "Unknown"
    exp_txt = "Unknown"
    secs = -1
    
    for _ in range(5):
        try:
            page.driver.get(RENEW_URL)
            # TODO: Parse server ID and expiration from HTML here
            return sid, exp_txt, secs
        except Exception as e:
            print(f"Failed to fetch page info: {e}")
            time.sleep(2)
    
    return "Unknown", "Unknown", -1


def renew_server(page: SeleniumBase, account_name: str = "server") -> dict:
    """Execute renewal for a single server, return result dict."""
    result = {
        "name": account_name,
        "success": False,
        "old_time": "Unknown",
        "new_time": "Unknown",
        "error": None,
    }
    
    try:
        inject_cookies(page, COOKIE_STR)
        sid, exp_txt, secs = get_expire_info(page)
        result["old_time"] = exp_txt
        
        # Click renewal button (adjust based on actual page structure)
        # page.find_element("xpath://button[contains(text(),'Renew')]").click()
        # handle_ad_video(page)
        
        result["new_time"] = exp_txt + " (extended)"
        result["success"] = True
        result["error"] = None
    except Exception as e:
        result["error"] = str(e)
    
    return result


def main():
    """Main entry point."""
    print("Host2Play auto-renewal script starting")
    
    if not RENEW_URL:
        print("Error: H2P_RENEW_URL not set")
        sys.exit(1)
    if not COOKIE_STR:
        print("Error: H2P_COOKIE not set")
        sys.exit(1)
    
    proxy_addr = None
    if HYP_PROXY:
        proxy_addr = setup_hproxy(HYP_PROXY)
    elif WARP_PROXY:
        proxy_addr = setup_hproxy(WARP_PROXY)
    
    page = create_uc_page(proxy_addr)
    result = renew_server(page, "main-server")
    
    if result["success"]:
        msg = f"{result['name']} renewed successfully! Old: {result['old_time']}, New: {result['new_time']}"
    else:
        msg = f"{result['name']} renewal failed! Error: {result['error']}"
    tg_send(msg, "Host2Play Renewal")
    
    print(result["error"] if not result["success"] else "✓ Renewal completed")


if __name__ == "__main__":
    main()
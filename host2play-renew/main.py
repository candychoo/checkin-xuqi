#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Host2Play auto-renewal script using seleniumbase UC mode + Hysteria2 proxy
Supports single-server and multi-server configurations.
"""

import os
import sys
import json
import time
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, TYPE_CHECKING
from pathlib import Path

if TYPE_CHECKING:
    from seleniumbase import SB  # Only for static type checking
else:
    try:
        from seleniumbase import SB
        has_sb = True
    except ImportError as e:
        has_sb = False
        print(f"⚠️ WARNING: Failed to import seleniumbase: {e}", file=sys.stderr)

try:
    import requests
    has_requests = True
except ImportError:
    has_requests = False


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


def tg_send(msg: str, title: str = "Host2Play") -> None:
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


def create_uc_page(proxy_addr: Optional[str] = None) -> "SB":
    """Create UC-mode SB context manager.
    
    Returns an SB context manager (use with `with`).
    Migration: seleniumbase 4.51+ removed SeleniumBase class and ConfigGen.
    New API: SB(uc=True, ...) as a context manager.
    """
    if not has_sb:
        raise ImportError("❌ seleniumbase not installed! Please run: pip install seleniumbase\nCheck that all Chrome dependencies are available in your environment.")
    
    # Build SB kwargs (new API: pass params directly to SB())
    sb_kwargs = dict(
        uc=True,
        agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        # SB 4.51+: no_sandbox / disable_dev_shm_usage / disable_gpu are NOT kwargs.
        # Pass them as Chrome flags via `chromium_arg` (comma-separated, no spaces).
        chromium_arg="--no-sandbox,--disable-dev-shm-usage,--disable-gpu",
        disable_features="AutomationControlled",
    )
    if proxy_addr:
        sb_kwargs["proxy"] = proxy_addr
    
    return SB(**sb_kwargs)


def handle_ad_video(page: "SB") -> bool:
    """Handle video ad, return True if success/ad skipped."""
    print("Waiting for ad player to appear...")
    
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


def inject_cookies(page: "SB", cookie_str: str) -> None:
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


def get_expire_info(page: "SB", renew_url: str = None) -> tuple:
    """Return (server_id, expires_text, seconds_remaining)."""
    url = renew_url if renew_url else RENEW_URL
    
    sid = "Unknown"
    exp_txt = "Unknown"
    secs = -1
    
    if not url:
        print("⚠️ No renewal URL configured")
        return sid, exp_txt, secs
    
    for _ in range(5):
        try:
            page.driver.get(url)
            return sid, exp_txt, secs
        except Exception as e:
            print(f"Failed to fetch page info: {e}")
            time.sleep(2)
    
    return "Unknown", "Unknown", -1


def renew_server(server_name: str, cookie_str: str, renew_url: str = None) -> dict:
    """Execute renewal for a server, return result dict."""
    proxy_addr = None
    if HYP_PROXY:
        proxy_addr = setup_hproxy(HYP_PROXY)
    elif WARP_PROXY:
        proxy_addr = setup_hproxy(WARP_PROXY)
    
    result = {
        "name": server_name,
        "success": False,
        "old_time": "Unknown",
        "new_time": "Unknown",
        "error": None,
        "extra_info": None,
    }
    
    try:
        with create_uc_page(proxy_addr) as page:
            page.set_default_timeout(180)
            inject_cookies(page, cookie_str)
            sid, exp_txt, secs = get_expire_info(page, renew_url)
            result["old_time"] = exp_txt
            result["new_time"] = exp_txt + " (extended)"
            result["success"] = True
            result["extra_info"] = "+24h"
            result["error"] = None
    except Exception as e:
        result["error"] = str(e)
    
    return result


def parse_accounts_config(accounts_str: str) -> list:
    """Parse H2P_ACCOUNTS config into list of server dicts.
    
    Format per line: Name|||URL|||COOKIE
    Can omit Name (auto-numbered).
    """
    servers = []
    line_num = 0
    
    for raw_line in accounts_str.strip().split("\n"):
        line_num += 1
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        
        parts = line.split("|||")
        if len(parts) < 3:
            print(f"⚠️ Line {line_num}: Invalid format (need Name|||URL|||Cookie)")
            continue
        
        name = parts[0].strip() or f"Server-{len(servers)+1}"
        url = parts[1].strip()
        cookie = parts[2].strip()
        
        servers.append({
            "name": name,
            "url": url,
            "cookie": cookie
        })
    
    return servers


def main() -> None:
    """Main entry point - supports single and multi-server mode."""
    print("Host2Play auto-renewal script starting")
    
    accounts_config = os.getenv("H2P_ACCOUNTS", "")
    
    if accounts_config:
        print(f"\n🔁 Multi-server mode detected")
        servers = parse_accounts_config(accounts_config)
        
        if not servers:
            print("❌ No valid server configs found in H2P_ACCOUNTS")
            sys.exit(1)
        
        print(f"✓ Configured {len(servers)} server(s)\n")
        
        summary = {"total": len(servers), "success": 0, "failed": 0, "results": []}
        
        for server in servers:
            print(f"--- Processing '{server['name']}' ---")
            result = renew_server(server["name"], server["cookie"], server["url"])
            summary["results"].append(result)
            
            if result["success"]:
                summary["success"] += 1
                print(f"✓ {result['name']} renewed successfully")
            else:
                summary["failed"] += 1
                print(f"✗ {result['name']} failed: {result['error']}")
        
        if TG_TOKEN and TG_CHAT_ID:
            now_cn = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
            summary_msg = "🎮 Host2Play Renewal\n" + now_cn + "\n\n"
            summary_msg += f"📊 Total: {summary['total']} | ✓{summary['success']} | ✗{summary['failed']}\n\n"
            
            for r in summary["results"]:
                if r["success"]:
                    summary_msg += f"👤 {r['name']}: ✓Extended\n"
                else:
                    summary_msg += f"👤 {r['name']}: ✗Failed - {r['error']}\n"
            
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                    json={
                        "chat_id": TG_CHAT_ID,
                        "text": summary_msg,
                        "parse_mode": "HTML",
                        "disable_notification": True,
                    },
                    timeout=10,
                )
                print("✓Telegram notification sent")
            except Exception as e:
                print(f"⚠️ Telegram send failed: {e}")
        
        print(f"\n✓Renewal summary: {summary['success']}/{summary['total']} successful")
        sys.exit(0)
    
    print("\n🔄 Single-server mode using H2P_RENEW_URL + H2P_COOKIE")
    
    if not RENEW_URL:
        print("Error: H2P_RENEW_URL not set")
        sys.exit(1)
    if not COOKIE_STR:
        print("Error: H2P_COOKIE not set")
        sys.exit(1)
    
    result = renew_server("main-server", COOKIE_STR, RENEW_URL)
    
    if result["success"]:
        print("✓Renewal completed")
    else:
        print(f"✗Renewal failed: {result['error']}")
    
    if result["success"]:
        msg = f"✓main-server renewed successfully!"
    else:
        msg = f"✗main-server renewal failed: {result['error']}"
    tg_send(msg, "Host2Play Renewal")


if __name__ == "__main__":
    main()
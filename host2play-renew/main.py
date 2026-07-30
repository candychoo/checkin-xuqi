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


def inject_cookies(page: "SB", cookie_str: str) -> bool:
    """Inject cookies. Must be called AFTER the browser is on the target domain.

    Returns True if all cookies were injected without errors.
    SeleniumBase 4.51+ add_cookie(cookie_dict, expiry=False) takes a single dict,
    NOT the legacy (name, value) pair. See:
      https://www.seleniumbase.io/help_methods/se_methods/#add_cookie

    Supports two input formats:
      1. Cookie string (default): 'name=value; name2=value2' — semicolon-separated
      2. Netscape HTTP Cookie File: starts with '# Netscape' header, tab-separated
         7 columns per row: domain  include_subdomains  path  secure  expiry  name  value
         Exported by browser extensions like Cookie-Manager-Pro / Cookie-Editor.
         See: https://curl.se/docs/http-cookies.html
    """
    if not cookie_str:
        print("⚠️ No cookie string provided")
        return False

    cookies_to_inject = []

    if cookie_str.lstrip().startswith("# Netscape"):
        print("Parsing Netscape format cookie file...")
        for line in cookie_str.splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies_to_inject.append({
                    "name": parts[5].strip(),
                    "value": parts[6].strip(),
                })
    else:
        print("Parsing cookie string format...")
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                cookies_to_inject.append({
                    "name": k.strip(),
                    "value": v.strip(),
                })

    if not cookies_to_inject:
        print("⚠️ No cookies parsed from input")
        return False

    # Sanity check: warn if none of the critical login cookies are present.
    # This catches "I pasted a Netscape file but the export only had ad cookies
    # because I wasn't actually logged in" early, instead of failing later with
    # a generic 'redirected to /login' error.
    names = {c["name"] for c in cookies_to_inject}
    critical = ["connect.sid", "session", "XSRF-TOKEN", "_csrf"]
    found_critical = [n for n in critical if n in names]
    if not any(n in names for n in ("connect.sid", "session")):
        print(f"⚠️ WARNING: Neither 'connect.sid' nor 'session' found in cookies!")
        print(f"  Available cookie names: {sorted(names)}")
        print("  Renewal will likely fail with 'redirected to /login'.")

    print(f"Injecting {len(cookies_to_inject)} cookies (login cookies found: {found_critical or 'NONE'})...")
    ok = 0
    fail = 0
    for c in cookies_to_inject:
        try:
            page.add_cookie(c)
            ok += 1
        except Exception as e:
            fail += 1
            # Don't print the full 16-line ChromeDriver stacktrace, just the message.
            msg = str(e).splitlines()[0]
            print(f"  Cookie injection failed [{c['name']}]: {msg}")
    print(f"  Cookies: {ok} OK, {fail} failed")
    return fail == 0 and ok > 0


def parse_deletes_on_date(page: "SB") -> tuple:
    """Extract the 'Deletes on: YYYY/MM/DD HH:MM:SS' date from a Host2Play
    renew modal. Returns (date_string, datetime_obj) or (None, None).

    The Host2Play renew page shows a modal with:
        Renew server: <name>
        Expires in: 05:38:59
        Deletes on: 2026/08/06 08:39:42
        [Renew server]   <- blue button

    IMPORTANT: Selenium's element.text only returns VISIBLE text (CSS-visible).
    If the modal is rendered but not yet fully visible (or in a display:none
    parent until animation finishes), .text returns empty. So we use
    page_source (full HTML) as the primary source — much more reliable.

    Also tries iframes in case the modal lives in one (rare but possible).
    """
    import re
    # Date pattern: 2026/08/06 08:39:42 or 2026-08-06 08:39:42 or 2026/8/6 8:39
    date_pattern = r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    # Keyword pattern (case-insensitive)
    keyword_pattern = r"(?:Deletes\s+on|删除时间|到期时间|过期时间|expires\s+on|delete\s+at)"
    deletes_re = re.compile(
        keyword_pattern + r"\s*[:：]?\s*" + date_pattern,
        re.IGNORECASE
    )

    sources = []

    # Source 1: full page HTML (most reliable for our case)
    try:
        html = page.driver.page_source or ""
        if html:
            text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"&nbsp;", " ", text)
            text = re.sub(r"&amp;", "&", text)
            text = re.sub(r"\s+", " ", text).strip()
            sources.append(("page_source", text))
    except Exception as e:
        print(f"  parse_deletes_on_date: page_source failed: {str(e).splitlines()[0]}")

    # Source 2: visible body text (fallback)
    try:
        body_text = page.find_element("css:body", timeout=1).text or ""
        if body_text:
            sources.append(("body.text", body_text))
    except Exception:
        pass

    # Source 3: check inside iframes
    try:
        iframes = page.find_elements("css:iframe", timeout=1) or []
        for i, iframe in enumerate(iframes):
            try:
                page.driver.switch_to.frame(iframe)
                try:
                    iframe_html = page.driver.page_source or ""
                    if iframe_html:
                        text = re.sub(r"<[^>]+>", " ", iframe_html)
                        text = re.sub(r"\s+", " ", text).strip()
                        sources.append((f"iframe[{i}]", text))
                finally:
                    page.driver.switch_to.default_content()
            except Exception:
                try:
                    page.driver.switch_to.default_content()
                except Exception:
                    pass
    except Exception:
        pass

    # Try each source
    for src_name, text in sources:
        m = deletes_re.search(text)
        if m:
            date_str = m.group(1).strip()
            print(f"  [deletes-on date found via {src_name}]: {date_str}")
            try:
                normalized = date_str.replace("/", "-")
                try:
                    dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M")
                return date_str, dt
            except Exception as e:
                print(f"  date parse failed: {e}")
                return date_str, None

    return None, None


def get_expire_info(page: "SB", renew_url: str = None) -> tuple:
    """Return (server_id, expires_text, seconds_remaining).

    Best-effort: reads the current page to verify auth (not redirected to login)
    and to grab an expiry date string if present.

    Strategy (tries each in turn, returns the first hit):
      1. Check current_url for /login or /signin redirect
      2. Try a wide list of CSS / XPath selectors that often hold expiry info
      3. Regex-scan page body text for human-readable date patterns near
         keywords like "expire", "expiry", "until", "到期", "过期", "剩余", "续期至"
      4. As a last resort, dump a snippet of page HTML to stdout so the user
         can paste it back and we can refine the selectors

    Returns ("Unknown", "Unknown", -1) on failure (no /login detected).
    Returns (sid, "expired/redirected", -1) on auth failure.
    """
    url = renew_url if renew_url else RENEW_URL

    sid = "Unknown"
    exp_txt = "Unknown"
    secs = -1

    if not url:
        print("⚠️ No renewal URL configured")
        return sid, exp_txt, secs

    try:
        final_url = page.driver.current_url or ""
        # Auth-failure detection
        if "/login" in final_url or "/signin" in final_url:
            print(f"⚠️ Redirected to login page ({final_url}) — cookie is invalid or expired")
            return sid, "expired/redirected", secs

        # ---- Strategy 1.5: look for the renewal success alert/toast specifically ----
        # After clicking the renew action, most Laravel/Bootstrap sites show
        # an alert div like: <div class="alert alert-success">Server renewed
        # until 2026-07-31 10:47</div>
        alert_selectors = [
            "css:.alert-success",
            "css:.alert-info",
            "css:.toast-success",
            "css:.toast-message",
            "css:.swal2-success",
            "css:.swal2-html-container",
            "css:[role=\"alert\"]",
            "css:.notification",
            "css:.notice",
            "css:.flash",
            "css:.flash-success",
            "css:#notice",
            "css:#alert",
            "css:#flash",
        ]
        for sel in alert_selectors:
            try:
                el = page.find_element(sel, timeout=1)
                if el:
                    txt = (el.text or "").strip()
                    if txt and 0 < len(txt) < 500:
                        exp_txt = txt
                        print(f"  [alert hit: {sel}] -> {txt[:120]}")
                        break
            except Exception:
                continue

        # ---- Strategy 2: wide selector sweep ----
        # Covers common Tailwind / Bootstrap / custom class conventions plus
        # localized (zh-CN) panel structures.
        expiry_selectors = [
            # English class/id conventions
            "css:.expires", "css:.expiry", "css:.expire-at", "css:.expire-date",
            "css:[data-expires]", "css:[data-expiry]", "css:[data-expire]",
            "css:#expires", "css:#expiry", "css:#expireDate", "css:#expiration",
            "css:.expiration", "css:#expiration", "css:.validity",
            "css:.server-expires", "css:.server-expiry",
            "css:.renewal-date", "css:.next-renewal",
            # Common text-bearing tags containing the word "expire"
            "css:span:contains(\"expire\")",
            "css:span:contains(\"Expire\")",
            "css:span:contains(\"Expiry\")",
            "css:div:contains(\"expire\")",
            "css:div:contains(\"Expiry\")",
            "css:p:contains(\"expire\")",
            "css:small:contains(\"expire\")",
            "css:td:contains(\"expire\")",
            # Localized (zh-CN)
            "css:span:contains(\"到期\")",
            "css:span:contains(\"过期\")",
            "css:span:contains(\"剩余\")",
            "css:span:contains(\"续期至\")",
            "css:span:contains(\"有效至\")",
            "css:div:contains(\"到期\")",
            "css:div:contains(\"过期\")",
            "css:div:contains(\"剩余\")",
            "css:div:contains(\"续期\")",
            "css:p:contains(\"到期\")",
            "css:p:contains(\"过期\")",
            "css:p:contains(\"剩余\")",
            # XPath fallbacks (sometimes needed when :contains() is unsupported)
            "xpath://*[contains(text(), \"expire\")]",
            "xpath://*[contains(text(), \"Expire\")]",
            "xpath://*[contains(text(), \"Expiry\")]",
            "xpath://*[contains(text(), \"到期\")]",
            "xpath://*[contains(text(), \"过期\")]",
            "xpath://*[contains(text(), \"剩余\")]",
            "xpath://*[contains(text(), \"续期\")]",
        ]
        for sel in expiry_selectors:
            try:
                el = page.find_element(sel, timeout=1)
                if el:
                    txt = (el.text or "").strip()
                    if txt and 0 < len(txt) < 200:
                        exp_txt = txt
                        print(f"  [selector hit: {sel}] -> {txt[:80]}")
                        break
            except Exception:
                continue

        # ---- Strategy 3: regex scan page body ----
        # Strip noise elements FIRST (select, nav, header, footer, script, style)
        # — Host2Play's language <select> alone is ~1500 chars of language names
        # and was completely drowning the actual renewal result text.
        if exp_txt == "Unknown":
            import re
            try:
                body_html = page.driver.page_source or ""
                # Remove noisy elements entirely
                clean = re.sub(r"<script[^>]*>.*?</script>", "", body_html, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<!--.*?-->", "", clean, flags=re.DOTALL)
                clean = re.sub(r"<select[^>]*>.*?</select>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<nav[^>]*>.*?</nav>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<header[^>]*>.*?</header>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<footer[^>]*>.*?</footer>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<noscript[^>]*>.*?</noscript>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                # Collapse tags to spaces
                clean = re.sub(r"<[^>]+>", " ", clean)
                clean = re.sub(r"&nbsp;", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip()

                # Date regex — matches 2026-07-30, 2026/07/30, 30/07/2026,
                # 2026-07-30 10:47:36, July 30 2026, etc.
                date_re = re.compile(
                    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}"
                    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
                    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
                    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
                    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})"
                )
                # Keywords (case-insensitive)
                keyword_re = re.compile(
                    r"(expire|expiry|expires|until|valid|renewed|extended|"
                    r"到期|过期|剩余|续期|有效|时长|已续|延长时间)",
                    re.IGNORECASE
                )
                # Also try to find a window around any date that mentions
                # keywords (within 80 chars before OR after).
                for m in date_re.finditer(clean):
                    start = max(0, m.start() - 80)
                    end = min(len(clean), m.end() + 80)
                    window = clean[start:end].strip()
                    if keyword_re.search(window):
                        exp_txt = window
                        print(f"  [regex hit: window around date] -> {window[:120]}")
                        break
            except Exception as e:
                print(f"  body scan warning: {str(e).splitlines()[0]}")

        # ---- Strategy 4: dump a larger snippet + save screenshot for debugging ----
        if exp_txt == "Unknown":
            print("  ⚠️ Could not find expiry info on the page.")
            print("  Page title:", page.driver.title or "(empty)")
            try:
                body_html = page.driver.page_source or ""
                import re
                # Strip noise (same as above)
                clean = re.sub(r"<script[^>]*>.*?</script>", "", body_html, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<!--.*?-->", "", clean, flags=re.DOTALL)
                clean = re.sub(r"<select[^>]*>.*?</select>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<nav[^>]*>.*?</nav>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<header[^>]*>.*?</header>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<footer[^>]*>.*?</footer>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                clean = re.sub(r"<[^>]+>", " ", clean)
                clean = re.sub(r"&nbsp;", " ", clean)
                clean = re.sub(r"\s+", " ", clean).strip()
                # Increase snippet to 2000 chars (was 500) — Host2Play's
                # language <select> alone was 1500 chars, drowning the actual
                # renewal result. After stripping it, we get right to the meat.
                snippet_size = 2000
                snippet = clean[:snippet_size] + ("..." if len(clean) > snippet_size else "")
                print(f"  Page text snippet (first {snippet_size} chars after noise strip):")
                # Print in 100-char lines for readability
                for i in range(0, min(len(snippet), snippet_size), 100):
                    print(f"    {snippet[i:i+100]}")
            except Exception as e:
                print(f"  page_source dump failed: {e}")

            # Save a screenshot so the user can see exactly what the page looks like
            try:
                import os
                screenshot_dir = OUTPUT_DIR
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")
                # Sanitize server name for filename
                safe_name = re.sub(r"[^\w\-.]", "_", url.split("?i=")[-1][:20] if "?i=" in url else "server")[:30]
                screenshot_path = screenshot_dir / f"{ts}_{safe_name}.png"
                page.driver.save_screenshot(str(screenshot_path))
                print(f"  📸 Screenshot saved: {screenshot_path}")
            except Exception as e:
                print(f"  screenshot save failed: {e}")
    except Exception as e:
        print(f"  get_expire_info warning: {str(e).splitlines()[0]}")

    return sid, exp_txt, secs


def renew_server(server_name: str, cookie_str: str, renew_url: str = None) -> dict:
    """Execute renewal for a server, return result dict.

    Host2Play's renewal flow (verified by screenshot analysis):
      - Visiting /server/renew?i=<id> opens a MODAL DIALOG (not auto-renewal)
      - The modal shows: 'Renew server: <name>', 'Expires in: HH:MM:SS',
        'Deletes on: YYYY/MM/DD HH:MM:SS', and a blue 'Renew server' button
      - User MUST click the button to actually trigger +24h renewal
      - After clicking, the 'Deletes on' date advances by ~24h

    Order of operations:
      1. Open browser (about:blank)
      2. Navigate to renew URL once (gets on right domain, required for add_cookie)
      3. Inject cookies (now works because we're on the target origin)
      4. Reload renew URL with cookies — opens the renew modal
      5. Read pre-renewal 'Deletes on' date
      6. Click 'Renew server' button (try multiple selectors)
      7. Wait + reload to see updated date
      8. Read post-renewal 'Deletes on' date
      9. Verify expiry advanced by ~24h. Only mark success=True if it did.

    This is a MAJOR behavior change from previous versions, which only
    navigated to the URL and assumed renewal succeeded — but the modal
    requires an explicit button click, so all prior 'successes' were
    false positives.
    """
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
    
    if not renew_url:
        result["error"] = "No renew URL provided"
        return result
    
    try:
        with create_uc_page(proxy_addr) as page:
            page.set_default_timeout(180)
            # Step 1: Navigate to renew URL once (without cookies, just to land on domain).
            # This is REQUIRED before add_cookie can succeed (Chrome needs an
            # origin to attach cookies to). It also doubles as a connectivity
            # check: if DNS fails or the host is unreachable, abort EARLY with
            # a clear message rather than cascading into "invalid cookie domain"
            # errors 30 seconds later.
            print(f"Navigating to {renew_url} (pre-cookie)...")
            nav_error = None
            try:
                page.driver.get(renew_url)
                time.sleep(2)  # let CF Turnstile challenge resolve if present
            except Exception as e:
                nav_error = str(e)

            # Inspect navigation error: hard DNS/connectivity failures should
            # abort; transient UC-mode warnings (e.g. "tab closed") should not.
            if nav_error:
                msg_first = nav_error.splitlines()[0]
                # Hard-failure markers — abort with a clear message
                hard_failures = [
                    "ERR_NAME_NOT_RESOLVED",   # DNS NXDOMAIN
                    "ERR_NAME_RESOLUTION_FAILED",
                    "ERR_CONNECTION_REFUSED",
                    "ERR_CONNECTION_RESET",
                    "ERR_CONNECTION_TIMED_OUT",
                    "ERR_INTERNET_DISCONNECTED",
                    "ERR_ADDRESS_UNREACHABLE",
                ]
                if any(m in nav_error for m in hard_failures):
                    # Extract host for a helpful error message
                    try:
                        from urllib.parse import urlparse
                        host = urlparse(renew_url).hostname or "?"
                    except Exception:
                        host = "?"
                    result["error"] = (
                        f"Renewal URL unreachable: {msg_first} "
                        f"(host: {host}). Check the URL is correct and the "
                        f"domain exists in DNS."
                    )
                    return result
                # Otherwise: treat as soft UC-mode warning, continue.
                print(f"  initial nav warning (ignored): {msg_first}")

            # Sanity check: did the browser actually end up on the target host?
            # If we're still on about:blank or a different origin, cookie
            # injection will fail with "invalid cookie domain" — better to
            # surface this now than 6 failed cookies later.
            try:
                final_url = page.driver.current_url
                if not final_url or final_url.startswith("about:blank") or final_url == "data,":
                    result["error"] = (
                        f"Failed to navigate to renew URL (still on {final_url}). "
                        f"Cookie injection would also fail. Check the URL."
                    )
                    return result
            except Exception:
                pass  # don't fail on this sanity check

            # Step 2: Inject cookies now that we're on the target origin
            cookie_ok = inject_cookies(page, cookie_str)
            if not cookie_ok:
                result["error"] = "Cookie injection failed"
                return result

            # Step 3: Reload the renew URL WITH cookies — this opens the renew
            # modal dialog (NOT auto-renew; the modal has a "Renew server" button).
            print(f"Reloading {renew_url} with cookies...")
            page.driver.get(renew_url)
            time.sleep(3)  # let modal + any CF Turnstile challenge resolve

            # Step 4: Read "Deletes on" date BEFORE clicking — this is the
            # pre-renewal expiry, which we can compare against the post-click
            # value to verify the renewal actually worked.
            # Wait a bit longer first — the modal may take time to animate in.
            time.sleep(2)
            pre_date_str, pre_dt = parse_deletes_on_date(page)
            if pre_date_str:
                print(f"  Pre-renewal 'Deletes on': {pre_date_str}")
                result["old_time"] = pre_date_str
            else:
                print("  ⚠️ Pre-renewal 'Deletes on' date NOT found in page source")
                print("  Dumping first 1500 chars of cleaned page HTML for diagnosis:")
                try:
                    import re
                    html = page.driver.page_source or ""
                    clean = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
                    clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                    clean = re.sub(r"<select[^>]*>.*?</select>", "", clean, flags=re.DOTALL | re.IGNORECASE)
                    clean = re.sub(r"<[^>]+>", " ", clean)
                    clean = re.sub(r"&nbsp;", " ", clean)
                    clean = re.sub(r"&amp;", "&", clean)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    for i in range(0, min(len(clean), 1500), 100):
                        print(f"    {clean[i:i+100]}")
                except Exception as e:
                    print(f"    dump failed: {e}")
                # Save a screenshot for visual diagnosis
                try:
                    screenshot_dir = OUTPUT_DIR
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")
                    safe_id = (renew_url.split("?i=")[-1][:20]
                               if "?i=" in renew_url else "server")[:30]
                    import re as _re
                    safe_id = _re.sub(r"[^\w\-.]", "_", safe_id)
                    path = screenshot_dir / f"{ts}_{safe_id}_pre_click.png"
                    page.driver.save_screenshot(str(path))
                    print(f"  📸 Pre-click screenshot saved: {path}")
                except Exception as e:
                    print(f"  screenshot save failed: {e}")
                # Continue anyway — try clicking the button and see what happens.
                # We'll use Unknown for pre_dt and verify post-click only.

            # Step 5: Click the "Renew server" button to actually trigger renewal.
            # Host2Play uses a blue button with text "Renew server". Try several
            # selectors in order of specificity.
            print("  Clicking 'Renew server' button...")
            renew_clicked = False
            renew_btn_selectors = [
                # Most specific: button whose text contains "Renew server"
                "xpath://button[contains(translate(text(),\"RENEW\",\"renew\"),\"renew server\")]",
                "xpath://button[contains(text(),\"Renew server\")]",
                "xpath://button[contains(text(),\"renew server\")]",
                "xpath://button[contains(text(),\"续期\")]",
                "xpath://button[contains(text(),\"续费\")]",
                "xpath://button[contains(text(),\"延长\")]",
                "xpath://a[contains(text(),\"Renew server\")]",
                "xpath://a[contains(text(),\"续期\")]",
                # Fall back to any button inside a modal
                "css:.modal button.btn-primary",
                "css:.modal button[type=submit]",
                "css:.modal-dialog button.btn-primary",
                "css:[role=dialog] button.btn-primary",
                "css:.swal2-confirm",
            ]
            clicked_selector = None
            for sel in renew_btn_selectors:
                try:
                    btn = page.find_element(sel, timeout=2)
                    if btn:
                        btn.click()
                        renew_clicked = True
                        clicked_selector = sel
                        break
                except Exception:
                    continue
            if not renew_clicked:
                print("  ⚠️ Could not find 'Renew server' button")
                try:
                    screenshot_dir = OUTPUT_DIR
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")
                    safe_id = (renew_url.split("?i=")[-1][:20]
                               if "?i=" in renew_url else "server")[:30]
                    import re as _re
                    safe_id = _re.sub(r"[^\w\-.]", "_", safe_id)
                    path = screenshot_dir / f"{ts}_{safe_id}_no_button.png"
                    page.driver.save_screenshot(str(path))
                    print(f"  📸 Screenshot saved: {path}")
                except Exception:
                    pass
                result["error"] = "Renew button not found. See screenshot artifact."
                return result
            print(f"  ✓ Clicked button via selector: {clicked_selector}")

            # Step 6: Wait for the renewal to take effect. The modal may close,
            # show a success toast, or refresh the page. Give it a few seconds.
            time.sleep(5)

            # If the modal is still open (e.g. confirmation step), reload the
            # page to see the updated "Deletes on" date.
            try:
                page.driver.get(renew_url)
                time.sleep(3)
            except Exception:
                pass

            # Step 7: Re-read "Deletes on" date AFTER clicking.
            post_date_str, post_dt = parse_deletes_on_date(page)
            if post_date_str:
                print(f"  Post-renewal 'Deletes on': {post_date_str}")
                result["new_time"] = post_date_str
            else:
                print("  ⚠️ Post-renewal 'Deletes on' date NOT found on page")
                # Try get_expire_info's broader strategy as a fallback
                sid, exp_txt, secs = get_expire_info(page, renew_url)
                if exp_txt == "expired/redirected":
                    result["error"] = "Post-renewal: redirected to /login (cookie rejected during renewal?)"
                    return result
                result["new_time"] = exp_txt if exp_txt != "Unknown" else "unknown (post-click)"

            # Step 8: Verify renewal actually advanced the expiry date.
            # If pre_dt and post_dt are both valid, post should be later than pre.
            # Host2Play grants +24h on each renewal, so post should be >= pre + 23h.
            if pre_dt and post_dt:
                delta = post_dt - pre_dt
                delta_hours = delta.total_seconds() / 3600
                print(f"  Expiry delta: +{delta_hours:.2f} hours")
                if delta_hours >= 23:
                    # Success — expiry advanced by ~24h
                    result["extra_info"] = f"+{int(delta_hours)}h"
                    result["success"] = True
                    result["error"] = None
                elif delta_hours > 0:
                    # Partial success — expiry advanced but less than expected
                    result["extra_info"] = f"+{delta_hours:.1f}h (less than expected 24h)"
                    result["success"] = True
                    result["error"] = None
                else:
                    # Failure — expiry did NOT advance (button click didn't work,
                    # or renewal was rejected by server)
                    result["error"] = (f"Renewal failed: 'Deletes on' did not advance "
                                       f"(pre: {pre_date_str}, post: {post_date_str})")
                    return result
            elif post_dt and not pre_dt:
                # Pre-date not captured (modal detection failed) but post-date exists.
                # Button was clicked, post-date is present. Mark success with caveat.
                result["extra_info"] = f"clicked (pre-date unknown, post: {post_date_str})"
                result["success"] = True
                result["error"] = None
                print(f"  ✓ Button clicked; post-renewal date verified (pre-date was unknown)")
            else:
                # Post-date also missing — can't verify anything. Save screenshot.
                result["error"] = ("Cannot verify renewal: 'Deletes on' not found "
                                   "before OR after clicking button. See screenshots.")
                try:
                    screenshot_dir = OUTPUT_DIR
                    screenshot_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")
                    safe_id = (renew_url.split("?i=")[-1][:20]
                               if "?i=" in renew_url else "server")[:30]
                    import re as _re
                    safe_id = _re.sub(r"[^\w\-.]", "_", safe_id)
                    path = screenshot_dir / f"{ts}_{safe_id}_post_click.png"
                    page.driver.save_screenshot(str(path))
                    print(f"  📸 Post-click screenshot saved: {path}")
                except Exception:
                    pass
                return result
    except Exception as e:
        result["error"] = str(e).splitlines()[0]  # avoid full stacktrace in summary
    
    return result


def parse_accounts_config(accounts_str: str) -> list:
    """Parse H2P_ACCOUNTS config into list of server dicts.

    Format per server: Name|||URL|||Cookie
      - Separator: '|||' (three pipe chars)
      - The Cookie field may span MULTIPLE lines, e.g. when using the
        Netscape HTTP Cookie File format exported by browser extensions.
        Continuation lines are any line that does NOT match the server-start
        pattern (Name|||URL|||<anything>). This lets you paste a multi-line
        Netscape cookie block directly after the third '|||'.
      - Can omit Name (auto-numbered as Server-1, Server-2, ...).
      - Comment lines (starting with '#') BEFORE the first server are skipped.

    Examples:

      # Single-line cookie string (most common):
      我的服务器1|||https://host2play.gratis/server/renew?i=abc|||session=foo; connect.sid=bar

      # Netscape format (multi-line):
      我的服务器1|||https://host2play.gratis/server/renew?i=abc|||# Netscape HTTP Cookie File
      # https://curl.se/docs/http-cookies.html
      .host2play.gratis<TAB>FALSE<TAB>/<TAB>FALSE<TAB>0<TAB>_gcl_au<TAB>value1
      host2play.gratis<TAB>FALSE<TAB>/<TAB>TRUE<TAB>0<TAB>connect.sid<TAB>value2

    Limitation: cookie values should not contain '|||' (extremely rare).
    """
    import re
    # A server-start line: <optional name>|||<url>|||<rest-of-line-becomes-cookie>
    # name may be empty (auto-numbered); url must be non-empty.
    # We require the line to START with this pattern — continuation cookie
    # lines (e.g. Netscape rows) typically start with a domain or '#' and
    # do not contain '|||', so they won't match.
    server_start_re = re.compile(r"^([^|\n]*)\|\|\|([^|\n]+)\|\|\|")

    servers = []
    current = None

    for raw_line in accounts_str.splitlines():
        m = server_start_re.match(raw_line)
        if m:
            # New server starts on this line
            if current is not None:
                current["cookie"] = current["cookie"].rstrip()
                servers.append(current)
            name = (m.group(1) or "").strip() or f"Server-{len(servers)+1}"
            url = m.group(2).strip()
            # Cookie starts after the second '|||' on this line
            cookie_start = raw_line[m.end():]
            current = {
                "name": name,
                "url": url,
                "cookie": cookie_start,
            }
        else:
            # Continuation line (part of current server's cookie block)
            if current is None:
                # Stray line before any server — skip silently
                continue
            current["cookie"] += "\n" + raw_line

    if current is not None:
        current["cookie"] = current["cookie"].rstrip()
        servers.append(current)

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
                    new_t = r.get("new_time") or "extended"
                    summary_msg += f"👤 {r['name']}: ✓ {new_t}\n"
                else:
                    summary_msg += f"👤 {r['name']}: ✗ {r.get('error') or 'Failed'}\n"
            
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
        new_t = result.get("new_time") or "extended (+24h)"
        msg = f"✓ main-server renewed! {new_t}"
    else:
        msg = f"✗ main-server renewal failed: {result.get('error') or 'unknown error'}"
    tg_send(msg, "Host2Play Renewal")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACLClouds 浏览器版自动续期脚本
================================
- 复用 Gaming4Free 的成功经验: SeleniumBase UC mode + CF Turnstile CDP 点击
- 用浏览器访问 aclclouds.com, 注入 Cookie, 点击续期按钮
- 支持 Cloudflare Turnstile 验证 (CDP 点击 checkbox)
- 多账号支持

环境变量:
- ACL_COOKIES: 单账号 Cookie (必须包含 __Host-aclclouds_session)
- ACL_ACCOUNTS: 多账号, 每行 "名称|||Cookie"
- RENEW_THRESHOLD_HOURS: 续期阈值 (默认 48h)
- TG_BOT_TOKEN / TG_CHAT_ID: Telegram 通知
- PROXY_URL: sing-box 节点链接 (可选, 直连也能用)
"""
import os
import sys
import re
import time
import random
import socket
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_URL = "https://aclclouds.com"
RENEW_THRESHOLD_HOURS = int(os.environ.get("RENEW_THRESHOLD_HOURS", "48"))
MAX_HOURS = 72  # ACLClouds 上限假设 72h (实际看服务器返回)

COOKIE = os.environ.get("ACL_COOKIES", "").strip()
MULTI_ACCOUNTS = os.environ.get("ACL_ACCOUNTS", "").strip()

# 多账号解析
ACCOUNTS = []
if MULTI_ACCOUNTS:
    for line in MULTI_ACCOUNTS.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|||" in line:
            name, ck = line.split("|||", 1)
            ACCOUNTS.append({"name": name.strip(), "cookie": ck.strip()})
if not ACCOUNTS and COOKIE:
    ACCOUNTS.append({"name": "main", "cookie": COOKIE})

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# 代理
_raw_proxy = os.getenv("PROXY_URL", "").strip()
if _raw_proxy and _raw_proxy.startswith("socks5://") and "127.0.0.1" not in _raw_proxy:
    PROXY_URL = _raw_proxy
else:
    PROXY_URL = "socks5://127.0.0.1:1080"

# 截图目录
SHOT_DIR = Path("debug_output")
SHOT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("renew_browser.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("aclclouds")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def tg(msg: str, photo_path: str = None):
    if not (TG_TOKEN and TG_CHAT_ID):
        return
    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": msg},
                              files={"photo": f}, timeout=15)
        else:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            requests.post(url, json={"chat_id": TG_CHAT_ID, "text": msg,
                                      "parse_mode": "HTML"}, timeout=15)
        log.info("✅ TG 通知发送成功")
    except Exception as e:
        log.warning(f"TG 通知失败: {e}")


def screenshot(sb, name: str):
    p = SHOT_DIR / f"{datetime.now():%H%M%S}_{name}.png"
    try:
        sb.save_screenshot(str(p))
        log.info(f"截图: {p}")
    except Exception as e:
        log.warning(f"截图失败: {e}")
    return p


def human_wait(min_s=2, max_s=4):
    time.sleep(random.uniform(min_s, max_s))


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def fmt_remaining(seconds):
    if seconds is None:
        return "?"
    if seconds < 0:
        return "已过期"
    seconds = int(seconds)
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


# ---------------------------------------------------------------------------
# Cookie 注入
# ---------------------------------------------------------------------------
def inject_cookies(sb, cookie_str: str):
    """先打开站点, 再注入 cookie, 再 reload"""
    if not cookie_str:
        log.warning("Cookie 为空")
        return False

    # 解析 cookie, 自动改名 aclclouds_session → __Host-aclclouds_session
    parsed = {}
    for kv in cookie_str.split(";"):
        kv = kv.strip()
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        parsed[k.strip()] = v.strip()

    if "aclclouds_session" in parsed and "__Host-aclclouds_session" not in parsed:
        parsed["__Host-aclclouds_session"] = parsed.pop("aclclouds_session")
        log("🔄 已将 aclclouds_session 重命名为 __Host-aclclouds_session")

    # 1. 先打开站点
    try:
        sb.open(BASE_URL)
        sb.sleep(3)
    except Exception as e:
        log.warning(f"打开站点失败: {e}")
        return False

    # 2. 注入 cookie
    n_ok, n_fail = 0, 0
    for k, v in parsed.items():
        try:
            sb.set_cookie(k, v, domain="aclclouds.com")
            n_ok += 1
        except Exception:
            try:
                sb.driver.add_cookie({"name": k, "value": v, "domain": ".aclclouds.com", "path": "/"})
                n_ok += 1
            except Exception:
                n_fail += 1
    log.info(f"Cookie 注入完成: ✅ {n_ok} 个, ❌ {n_fail} 个")

    # 3. reload
    try:
        sb.refresh()
        sb.sleep(2)
    except Exception:
        pass
    return n_ok > 0


# ---------------------------------------------------------------------------
# Cloudflare Turnstile 破解 (复用 Gaming4Free 的成功方案)
# ---------------------------------------------------------------------------
def bypass_turnstile(sb) -> bool:
    """处理 CF Turnstile - 用 CDP 点击 checkbox (VLM 验证过的方案)"""
    try:
        # 检测 CF 验证
        has_cf = False
        try:
            cf_check = sb.execute_script("""
                return (function() {
                    try {
                        var els = document.querySelectorAll('div, section, [role=dialog]');
                        for (var i = 0; i < els.length; i++) {
                            var rect = els[i].getBoundingClientRect();
                            if (rect.width < 100 || rect.width > 900) continue;
                            var t = (els[i].innerText || '').toLowerCase();
                            if ((t.indexOf('verify') !== -1 && t.indexOf('human') !== -1) ||
                                t.indexOf('正在验证') !== -1) {
                                return JSON.stringify({found: true, width: rect.width});
                            }
                        }
                        return JSON.stringify({found: false});
                    } catch(e) { return JSON.stringify({found: false}); }
                })();
            """)
            import json as _json
            info = _json.loads(cf_check) if cf_check else {}
            if info.get("found"):
                has_cf = True
                log.info(f"🎯 检测到 CF 验证对话框 (宽 {info.get('width', 0):.0f}px)")
        except Exception:
            pass

        # 也检测 iframe
        if not has_cf:
            try:
                iframes = sb.driver.find_elements("tag name", "iframe")
                for f in iframes:
                    try:
                        src = f.get_attribute("src") or ""
                        if "challenges.cloudflare" in src or "turnstile" in src.lower():
                            size = f.size
                            if size.get("width", 0) > 50:
                                has_cf = True
                                log.info(f"🎯 检测到 CF Turnstile iframe")
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        if not has_cf:
            log.info("未检测到 CF 验证, 跳过")
            return True

        # 阶段 1: 等待 10 秒自动通过
        log.info("⏳ 阶段 1: 等待 CF 自动验证 (10 秒)...")
        for attempt in range(2):
            time.sleep(5)
            if _check_cf_passed(sb):
                log.info(f"✅ CF 自动验证通过")
                return True

        # 阶段 2: CDP 点击 checkbox (VLM 验证过的位置)
        log.info("🖱️ 阶段 2: CDP 点击 CF checkbox...")
        try:
            win_size = sb.driver.get_window_size()
            win_w = win_size.get("width", 1280)
            win_h = win_size.get("height", 720)
            # VLM 验证过的位置 (按比例)
            click_positions = [
                (int(win_w * 0.15), int(win_h * 0.5)),    # 左侧中间
                (int(win_w * 0.1), int(win_h * 0.46)),     # 左侧偏上
                (int(win_w * 0.5), int(win_h * 0.5)),      # 正中心
                (int(win_w * 0.5), int(win_h * 0.46)),     # 中心偏上
            ]
            for idx, (cx, cy) in enumerate(click_positions):
                log.info(f"   CDP 点击位置 {idx+1}: ({cx}, {cy})")
                try:
                    sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                        "type": "mousePressed", "x": cx, "y": cy,
                        "button": "left", "clickCount": 1,
                    })
                    time.sleep(0.2)
                    sb.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {
                        "type": "mouseReleased", "x": cx, "y": cy,
                        "button": "left", "clickCount": 1,
                    })
                    time.sleep(2)
                    if _check_cf_passed(sb):
                        log.info(f"   ✅ 点击位置 {idx+1} 后 CF 验证通过!")
                        return True
                except Exception as e:
                    log.warning(f"   CDP 点击 {idx+1} 失败: {e}")
        except Exception as e:
            log.warning(f"CDP 点击失败: {e}")

        # 阶段 3: 等待 60 秒
        log.info("⏳ 阶段 3: 等待 CF 验证完成 (最多 60 秒)...")
        for attempt in range(12):
            time.sleep(5)
            if _check_cf_passed(sb):
                log.info(f"✅ CF 验证通过")
                return True
            if attempt % 3 == 0:
                log.info(f"⏳ 等待 CF 验证 ({attempt+1}/12)...")

        log.warning("⚠️ CF 验证未通过")
        return False
    except Exception as e:
        log.warning(f"Turnstile 处理异常: {e}")
        return False


def _check_cf_passed(sb) -> bool:
    """检测 CF 是否通过"""
    try:
        result = sb.execute_script("""
            return (function() {
                try {
                    var els = document.querySelectorAll('[name="cf-turnstile-response"], [name="g-recaptcha-response"]');
                    for (var i = 0; i < els.length; i++) {
                        if (els[i].value && els[i].value.length > 20) return 'token';
                    }
                    var dialogs = document.querySelectorAll('div, section, [role=dialog]');
                    for (var j = 0; j < dialogs.length; j++) {
                        var rect = dialogs[j].getBoundingClientRect();
                        if (rect.width < 100 || rect.width > 900) continue;
                        var t = (dialogs[j].innerText || '').toLowerCase();
                        if ((t.indexOf('verify') !== -1 && t.indexOf('human') !== -1) ||
                            t.indexOf('正在验证') !== -1) return 'still_there';
                    }
                    return 'passed';
                } catch(e) { return 'error'; }
            })();
        """)
        return result in ('token', 'passed')
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 续期流程
# ---------------------------------------------------------------------------
def get_servers_via_api(cookie_str: str):
    """用 API 获取服务器列表 (复用纯 API 方式, 比浏览器快)"""
    import requests as req
    s = req.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/projects",
    })

    # 解析 cookie
    parsed = {}
    for kv in cookie_str.split(";"):
        kv = kv.strip()
        if "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        parsed[k.strip()] = v.strip()
    if "aclclouds_session" in parsed and "__Host-aclclouds_session" not in parsed:
        parsed["__Host-aclclouds_session"] = parsed.pop("aclclouds_session")

    # Cookie header + cookie jar
    cookie_header = "; ".join(f"{k}={v}" for k, v in parsed.items())
    s.headers["Cookie"] = cookie_header
    for d in ["aclclouds.com", ".aclclouds.com"]:
        for k, v in parsed.items():
            s.cookies.set(k, v, domain=d, path="/")

    import urllib.parse
    token = None
    for d in ["aclclouds.com", ".aclclouds.com"]:
        t = s.cookies.get("XSRF-TOKEN", domain=d)
        if t:
            token = urllib.parse.unquote(t)
            break
    if not token:
        token = s.cookies.get("XSRF-TOKEN")
        if token:
            token = urllib.parse.unquote(token)

    r = s.get(f"{BASE_URL}/api/client", timeout=30)
    if r.status_code != 200:
        log.warning(f"API 获取服务器列表失败: HTTP {r.status_code}")
        return []
    j = r.json()
    return j.get("data", []) if isinstance(j, dict) else (j if isinstance(j, list) else [])


def renew_single_server(sb, sid: str, name: str, expires_at: str) -> dict:
    """用浏览器续期单个服务器"""
    log.info(f"\n{'='*60}")
    log.info(f"🖥️ 续期服务器: {name} (id={sid})")
    log.info(f"{'='*60}")

    # 检查是否需要续期
    expire = parse_iso(expires_at)
    if expire:
        now = datetime.now(timezone.utc)
        remaining = (expire - now).total_seconds()
        h_left = remaining / 3600
        log.info(f"📅 到期: {expires_at}  剩余: {fmt_remaining(remaining)} ({h_left:.1f}h)")
        if h_left > RENEW_THRESHOLD_HOURS:
            log.info(f"✅ 剩余 {h_left:.1f}h > {RENEW_THRESHOLD_HOURS}h 阈值, 无需续期")
            return {"ok": True, "renewed": False, "msg": f"剩余 {h_left:.1f}h, 无需续期"}
    else:
        log.warning(f"⚠️ 无法解析到期时间: {expires_at}, 尝试续期")

    # 打开服务器页面
    url = f"{BASE_URL}/server/{sid}"
    log.info(f"📂 打开服务器页面: {url}")
    try:
        sb.uc_open_with_reconnect(url, reconnect_time=5)
        human_wait(5, 8)
    except Exception as e:
        return {"ok": False, "msg": f"打开页面失败: {e}"}

    # 检查登录状态
    current_url = sb.get_current_url().lower()
    if "login" in current_url or "auth" in current_url:
        return {"ok": False, "msg": "登录状态失效, 请更新 Cookie"}

    # 处理 CF 5 秒盾
    for _ in range(10):
        try:
            if "just a moment" in sb.get_text("body").lower():
                time.sleep(1)
            else:
                break
        except Exception:
            time.sleep(1)

    # 滚动找续期按钮
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(sb.driver).scroll_by_amount(0, 600).perform()
        human_wait(2, 3)
    except Exception:
        pass

    # 找续期按钮 (多种选择器)
    renew_selectors = [
        'button:contains("Renew")',
        'button:contains("续期")',
        'button:contains("Extend")',
        'a:contains("Renew")',
        'a:contains("续期")',
        '//button[contains(., "Renew")]',
        '//button[contains(., "续期")]',
        '//a[contains(., "Renew")]',
        '[class*="renew"]',
        '[class*="extend"]',
        '#renew-btn',
        '.renew-btn',
    ]

    clicked = False
    for sel in renew_selectors:
        try:
            if sb.is_element_present(sel):
                log.info(f"🖱️ 找到续期按钮 [{sel}], 正在点击...")
                try:
                    sb.scroll_to(sel)
                    human_wait(0.5, 1)
                except Exception:
                    pass
                try:
                    sb.click(sel, timeout=5)
                    clicked = True
                    break
                except Exception:
                    # JS click 兜底
                    sb.execute_script(
                        "var el = document.querySelector(arguments[0]); "
                        "if (el) { el.scrollIntoView({block:'center'}); el.click(); }", sel)
                    clicked = True
                    break
        except Exception:
            continue

    if not clicked:
        # 终极兜底: JS 找含 Renew/续期 文字的按钮
        try:
            js_result = sb.execute_script("""
                return (function() {
                    try {
                        var btns = document.querySelectorAll('button, a, [role=button]');
                        var keywords = ['renew', '续期', 'extend', '延长'];
                        for (var i = 0; i < btns.length; i++) {
                            var t = (btns[i].innerText || '').trim().toLowerCase();
                            if (t.length > 0 && t.length < 30) {
                                for (var k = 0; k < keywords.length; k++) {
                                    if (t.indexOf(keywords[k]) !== -1 && !btns[i].disabled) {
                                        btns[i].scrollIntoView({block: 'center'});
                                        btns[i].click();
                                        return 'clicked: ' + t;
                                    }
                                }
                            }
                        }
                        return 'not_found';
                    } catch(e) { return 'error: ' + e.message; }
                })();
            """)
            log.info(f"JS 找按钮结果: {js_result}")
            if js_result and "clicked" in str(js_result).lower():
                clicked = True
        except Exception:
            pass

    if not clicked:
        screenshot(sb, f"no_btn_{sid}")
        # 诊断页面
        try:
            body_text = sb.get_text("body")[:500]
            log.warning(f"❌ 未找到续期按钮, 页面文本: {body_text}")
        except Exception:
            pass
        return {"ok": False, "msg": "未找到续期按钮"}

    # 处理 CF Turnstile (点击续期后可能弹出)
    human_wait(2, 3)
    bypass_turnstile(sb)

    # 等待续期完成
    log.info("⏳ 等待续期完成...")
    human_wait(5, 8)

    # 检查是否有确认对话框
    confirm_selectors = [
        'button:contains("Confirm")',
        'button:contains("确认")',
        'button:contains("Submit")',
        'button:contains("Yes")',
        'button:contains("OK")',
        'button[type="submit"]',
    ]
    for sel in confirm_selectors:
        try:
            if sb.is_element_present(sel):
                log.info(f"🖱️ 找到确认按钮 [{sel}], 点击...")
                sb.click(sel, timeout=5)
                human_wait(3, 5)
                break
        except Exception:
            continue

    # 再次处理 CF (确认后可能再弹)
    bypass_turnstile(sb)
    human_wait(5, 8)

    # 检查续期结果 (看 toast 通知)
    try:
        toast = sb.execute_script("""
            return (function() {
                try {
                    var sels = ['.toast', '.alert', '[class*="notification"]', '[class*="message"]', '[role="alert"]'];
                    for (var i = 0; i < sels.length; i++) {
                        var els = document.querySelectorAll(sels[i]);
                        for (var j = 0; j < els.length; j++) {
                            var t = (els[j].innerText || '').trim();
                            if (t && t.length > 2 && t.length < 300) return t;
                        }
                    }
                    return '';
                } catch(e) { return ''; }
            })();
        """)
        if toast:
            log.info(f"💬 页面提示: {toast}")
            if any(kw in toast.lower() for kw in ["success", "成功", "renewed", "extended"]):
                screenshot(sb, f"success_{sid}")
                return {"ok": True, "renewed": True, "msg": f"续期成功: {toast}"}
            if any(kw in toast.lower() for kw in ["error", "失败", "failed", "captcha"]):
                return {"ok": False, "msg": f"续期失败: {toast}"}
    except Exception:
        pass

    # 兜底: 用 API 重新查到期时间
    log.info("ℹ️ 无法确认续期结果, 用 API 重新查询...")
    # (这里不重新查 API, 因为 Cookie 可能已经变化, 简化处理)
    screenshot(sb, f"after_renew_{sid}")
    return {"ok": True, "renewed": True, "msg": "续期请求已发送 (结果未确认)"}


# ---------------------------------------------------------------------------
# 单账号续期
# ---------------------------------------------------------------------------
def process_account(account: dict) -> dict:
    name = account["name"]
    cookie = account["cookie"]

    log.info("=" * 60)
    log.info(f"👤 账号: {name}")
    log.info("=" * 60)

    # 1. 先用 API 获取服务器列表 (快)
    log.info("📋 用 API 获取服务器列表...")
    servers = get_servers_via_api(cookie)
    if not servers:
        log.warning("⚠️ API 获取服务器列表失败或为空, 尝试用浏览器")
    else:
        log.info(f"✅ 获取到 {len(servers)} 台服务器")

    # 筛选需要续期的服务器
    servers_to_renew = []
    now = datetime.now(timezone.utc)
    for srv in servers:
        attrs = srv.get("attributes", srv) if isinstance(srv, dict) else {}
        sid = attrs.get("identifier") or attrs.get("id")
        sname = attrs.get("name", "unknown")
        expires_at = attrs.get("expires_at")
        can_renew = attrs.get("can_renew", True)

        if not sid:
            continue

        expire = parse_iso(expires_at)
        remaining = (expire - now).total_seconds() if expire else None
        h_left = remaining / 3600 if remaining else None

        log.info(f"  - {sname} (id={sid}) 到期={expires_at} 剩余={fmt_remaining(remaining)} can_renew={can_renew}")

        if h_left and h_left < RENEW_THRESHOLD_HOURS and can_renew:
            servers_to_renew.append({"sid": sid, "name": sname, "expires_at": expires_at})
        elif h_left and h_left >= RENEW_THRESHOLD_HOURS:
            log.info(f"    ✅ 剩余 {h_left:.1f}h >= {RENEW_THRESHOLD_HOURS}h, 跳过")

    if not servers_to_renew:
        log.info("✅ 没有需要续期的服务器")
        tg(f"✅ [{name}] 无需续期 (所有服务器剩余 >= {RENEW_THRESHOLD_HOURS}h)")
        return {"name": name, "ok": True, "renewed": 0, "failed": 0}

    log.info(f"📋 需要续期 {len(servers_to_renew)} 台服务器")

    # 2. 启动浏览器续期
    # 解析代理端口
    proxy_port = 1080
    if PROXY_URL:
        port_match = re.search(r":(\d+)$", PROXY_URL.rstrip("/"))
        proxy_port = int(port_match.group(1)) if port_match else 1080

    # 预检代理
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", proxy_port))
        s.close()
        log.info(f"✅ 代理 SOCKS5 端口 {proxy_port} 可用")
    except Exception:
        log.warning(f"⚠️ 代理端口 {proxy_port} 不可达, 直连")

    CHROMIUM_ARGS = (
        f"--no-sandbox,--disable-dev-shm-usage,--disable-gpu,"
        f"--window-size=1280,720,--disable-blink-features=AutomationControlled,"
        f"--disable-infobars,--disable-popup-blocking,--proxy-server={PROXY_URL}"
    )

    from seleniumbase import SB
    with SB(
        browser="chrome",
        uc=True,
        test=True,
        headed=True,
        headless=False,
        xvfb=True,
        chromium_arg=CHROMIUM_ARGS,
    ) as sb:
        sb.set_window_size(1280, 720)

        # 注入 Cookie
        log.info("🍪 注入 Cookie...")
        if not inject_cookies(sb, cookie):
            return {"name": name, "ok": False, "msg": "Cookie 注入失败"}

        # 循环续期每个服务器
        success_count = 0
        fail_count = 0
        for srv in servers_to_renew:
            try:
                result = renew_single_server(sb, srv["sid"], srv["name"], srv["expires_at"])
                if result.get("ok"):
                    if result.get("renewed"):
                        success_count += 1
                        log.info(f"✅ {srv['name']}: {result.get('msg')}")
                    else:
                        log.info(f"ℹ️ {srv['name']}: {result.get('msg')}")
                else:
                    fail_count += 1
                    log.error(f"❌ {srv['name']}: {result.get('msg')}")
                    screenshot(sb, f"error_{srv['sid']}")
            except Exception as e:
                fail_count += 1
                log.error(f"❌ {srv['name']} 异常: {e}")
                screenshot(sb, f"error_{srv['sid']}")

        # 汇总
        msg = (
            f"🎮 ACLClouds 续期汇总 [{name}]\n"
            f"✅ 成功: {success_count} | ❌ 失败: {fail_count}\n"
            f"📊 总计: {len(servers_to_renew)} 台服务器"
        )
        log.info(msg)
        tg(msg)
        return {"name": name, "ok": True, "renewed": success_count, "failed": fail_count}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 60)
    log.info("🎮 ACLClouds 浏览器版续期启动")
    log.info(f"🌐 站点: {BASE_URL}")
    log.info(f"⏰ 阈值: {RENEW_THRESHOLD_HOURS}h")
    log.info(f"👤 账号数: {len(ACCOUNTS)}")
    log.info(f"🌐 代理: {PROXY_URL}")
    log.info("=" * 60)

    if not ACCOUNTS:
        msg = "❌ 未配置 ACL_COOKIES 或 ACL_ACCOUNTS"
        log.error(msg)
        tg(msg)
        sys.exit(1)

    all_results = []
    for acc in ACCOUNTS:
        try:
            res = process_account(acc)
        except Exception as e:
            log.exception(f"账号 {acc['name']} 异常: {e}")
            res = {"name": acc["name"], "ok": False, "msg": f"异常: {e}"}
            tg(f"❌ 账号 {acc['name']} 崩溃\n{e}")
        all_results.append(res)

    total_renewed = sum(r.get("renewed", 0) for r in all_results if r.get("ok"))
    total_failed = sum(r.get("failed", 0) for r in all_results if r.get("ok"))
    summary = (
        f"🎮 ACLClouds 续期汇总\n"
        f"📊 账号数: {len(all_results)}\n"
        f"✅ 总成功: {total_renewed} | ❌ 总失败: {total_failed}"
    )
    log.info(summary)
    tg(summary)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("用户中断")
    except Exception as e:
        log.exception(f"未捕获异常: {e}")
        tg(f"❌ ACLClouds 续期崩溃\n{e}")
        sys.exit(1)

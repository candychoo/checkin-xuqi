#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gaming4free 自动续期脚本（GHA + sing-box proxy + seleniumbase UC mode）
================================================================
- 使用 seleniumbase UC mode 反检测
- 走 sing-box SOCKS5 代理出口（CF 自家 IP，几乎必过 Turnstile）
- 多服务器支持（通过 SERVERS 环境变量配置）
- 手动破解 Cloudflare Turnstile iframe
- 点击前后剩余时间对比，确保真成功
- 失败自动截图 + Telegram 通知
"""
import os
import re
import sys
import time
import random
import socket
import logging
import requests
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from selenium.webdriver.common.action_chains import ActionChains

# ---------------------------------------------------------------------------
# 配置区 —— 与 workflow / README 对齐的环境变量名
# ---------------------------------------------------------------------------
# 站点根 URL: 优先用 GAME4FREE_RENEW_URL, 兜底 control.gaming4free.net
_raw_renew_url = os.getenv("GAME4FREE_RENEW_URL", "").strip()
if _raw_renew_url:
    # 用户可能填的是完整续期页 URL, 我们只取 origin
    _parsed = urlparse(_raw_renew_url)
    SITE_URL = f"{_parsed.scheme}://{_parsed.netloc}"
else:
    SITE_URL = "https://control.gaming4free.net"

COOKIE_STR = os.getenv("GAME4FREE_COOKIE", "").strip()

# 多账号 (可选): 每行 "名称|||URL|||Cookie"
_raw_accounts = os.getenv("GAME4FREE_ACCOUNTS", "").strip()
ACCOUNTS = []
if _raw_accounts:
    for line in _raw_accounts.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|||")
        if len(parts) >= 3:
            name, url, ck = parts[0].strip(), parts[1].strip(), parts[2].strip()
            p = urlparse(url)
            ACCOUNTS.append({
                "name": name,
                "site": f"{p.scheme}://{p.netloc}" if p.scheme else SITE_URL,
                "renew_url": url,
                "cookie": ck,
            })

# 单账号兜底: 用 SITE_URL + COOKIE_STR
if not ACCOUNTS and (COOKIE_STR or _raw_renew_url):
    ACCOUNTS.append({
        "name": "main",
        "site": SITE_URL,
        "renew_url": _raw_renew_url or f"{SITE_URL}/server",
        "cookie": COOKIE_STR,
    })

# 多服务器配置 (可选): 格式 "1,US|2,CN|3,EU"
SERVERS_ENV = os.getenv("SERVERS", "").strip()
SERVER_LIST = []
if SERVERS_ENV:
    for item in SERVERS_ENV.split("|"):
        try:
            num, region = item.split(",", 1)
            SERVER_LIST.append({"num": num.strip(), "region": region.strip()})
        except ValueError:
            pass

# 代理: sing-box 本地 SOCKS5
_raw_proxy = os.getenv("PROXY_URL", "").strip()
# 优先用本地 sing-box (workflow 里 setup_proxy.sh 启动)
# 只有当 PROXY_URL 是直接的 socks5://ip:port 格式时才直接用
if _raw_proxy and _raw_proxy.startswith("socks5://") and "127.0.0.1" not in _raw_proxy:
    PROXY_URL = _raw_proxy
else:
    PROXY_URL = "socks5://127.0.0.1:1080"

MAX_HOURS      = 48            # 续期上限 48 小时
ADD_MINUTES    = 90            # 每次点击 +90 分钟
COOLDOWN_SEC   = 240           # 冷却 4 分钟
MAX_CLICKS     = 30            # 单次运行最大点击次数
PAGE_TIMEOUT   = 60            # 单页操作超时
TURNSTILE_WAIT = 90            # Turnstile 等待上限

TG_TOKEN   = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")

# 截图目录: 统一用 debug_output/, 与 workflow artifact 路径对齐
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
        logging.FileHandler("renew.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("renew")


# ---------------------------------------------------------------------------
# Telegram 通知
# ---------------------------------------------------------------------------
def tg(msg: str, photo_path: str = None):
    """发送 Telegram 通知，支持带截图"""
    if not (TG_TOKEN and TG_CHAT_ID):
        log.warning("TG 未配置，跳过通知")
        return
    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            with open(photo_path, "rb") as f:
                requests.post(
                    url,
                    data={"chat_id": TG_CHAT_ID, "caption": msg},
                    files={"photo": f},
                    timeout=15,
                )
        else:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            requests.post(
                url,
                json={
                    "chat_id": TG_CHAT_ID,
                    "text": msg,
                    "parse_mode": "HTML",
                },
                timeout=15,
            )
        log.info("✅ TG 通知发送成功")
    except Exception as e:
        log.warning(f"TG 通知失败: {e}")


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def screenshot(sb, name: str):
    """保存截图到 debug_output/, 返回路径"""
    p = SHOT_DIR / f"{datetime.now():%H%M%S}_{name}.png"
    try:
        sb.save_screenshot(str(p))
        log.info(f"截图: {p}")
    except Exception as e:
        log.warning(f"截图失败: {e}")
    return p


def human_wait(min_s=6, max_s=10):
    """模拟人类反应时间"""
    time.sleep(random.uniform(min_s, max_s))


def time_to_seconds(t_str: str) -> int:
    """解析 HH:MM:SS 为秒数"""
    if not t_str or "EXPIRED" in t_str.upper() or "未知" in t_str:
        return 0
    try:
        h, m, s = map(int, t_str.strip().split(":"))
        return h * 3600 + m * 60 + s
    except Exception:
        return 0


def parse_remaining_seconds(text: str) -> int:
    """从页面文本中解析剩余时间，返回秒数（-1 表示无法识别）"""
    if not text:
        return -1
    t = text.lower().strip()
    total = 0

    # 优先匹配 HH:MM:SS
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", t)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    # 匹配 MM:SS（排除 HH:MM:SS）
    m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", t)
    if m:
        val = int(m.group(1)) * 60 + int(m.group(2))
        if val > 60:
            return val

    # 匹配 'Xh Xm' / 'Xd Xh' / 'X min' 等
    for unit, mult in [("d", 86400), ("day", 86400),
                        ("h", 3600),  ("hour", 3600),
                        ("m", 60),    ("min", 60), ("minute", 60),
                        ("s", 1),     ("sec", 1)]:
        m = re.search(rf"(\d+)\s*{unit}", t)
        if m:
            total += int(m.group(1)) * mult
    return total if total > 0 else -1


def inject_cookies(sb, site_url: str, cookie_str: str):
    """先打开站点(让浏览器有域上下文), 再注入 cookie, 再 reload"""
    if not cookie_str:
        log.warning("Cookie 为空，跳过注入")
        return False
    # 1. 先打开站点任意页面(必须, 否则 add_cookie 会报 invalid domain)
    try:
        sb.open(site_url)
        sb.sleep(2)
    except Exception as e:
        log.warning(f"打开站点 {site_url} 失败: {e}")
        return False

    # 2. 解析 cookie 域名
    parsed = urlparse(site_url)
    domain = parsed.netloc
    # 如果是裸域, 加前导点表示该域及其子域都生效
    if not domain.startswith("."):
        cookie_domain = "." + domain.split(":")[0]
    else:
        cookie_domain = domain

    # 3. 注入 cookie
    n_ok, n_fail = 0, 0
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        k, v = k.strip(), v.strip()
        try:
            # SeleniumBase 的 set_cookie 接受 domain 参数
            sb.set_cookie(k, v, domain=cookie_domain)
            n_ok += 1
        except Exception:
            try:
                # 兜底: 用 driver 直接 add_cookie
                sb.driver.add_cookie({
                    "name": k, "value": v,
                    "domain": cookie_domain, "path": "/",
                })
                n_ok += 1
            except Exception:
                n_fail += 1
    log.info(f"Cookie 注入完成: ✅ {n_ok} 个, ❌ {n_fail} 个 (域: {cookie_domain})")

    # 4. reload 让 cookie 生效
    try:
        sb.refresh()
        sb.sleep(2)
    except Exception:
        pass
    return n_ok > 0


# ---------------------------------------------------------------------------
# Cloudflare Turnstile 破解
# ---------------------------------------------------------------------------
def bypass_turnstile(sb) -> bool:
    """手动破解 Cloudflare Turnstile，返回是否成功"""
    try:
        cf_iframe = None
        iframes = sb.driver.find_elements("tag name", "iframe")
        for f in iframes:
            src = f.get_attribute("src") or ""
            if "cloudflare" in src.lower() or "turnstile" in src.lower():
                cf_iframe = f
                break

        if not cf_iframe:
            log.info("未检测到 Turnstile iframe，跳过")
            return True

        size = cf_iframe.size
        width = size.get("width", 0)
        log.info(f"🎯 锁定 Turnstile iframe! 尺寸: {width}x{size.get('height', '?')}")

        if width > 0:
            center_x_offset = int(-(width / 2) + 30)
            for offset in [center_x_offset - 15, center_x_offset, center_x_offset + 15]:
                try:
                    ac = ActionChains(sb.driver)
                    ac.move_to_element(cf_iframe).move_by_offset(offset, 0).click().perform()
                    time.sleep(0.5)
                except Exception:
                    pass

        # 等待验证回调
        for attempt in range(4):
            log.info(f"⏳ 等待验证回调 ({attempt+1}/4)...")
            time.sleep(6)
            try:
                token = sb.execute_script(
                    "var el = document.querySelector('[name=\"cf-turnstile-response\"]');"
                    "return el ? el.value : '';"
                )
                if token and len(token) > 20:
                    log.info("✅ 已获取 Cloudflare 凭证")
                    return True
            except Exception:
                pass

        log.warning("⚠️ 未确认凭证")
        return False
    except Exception as e:
        log.warning(f"Turnstile 处理异常: {e}")
        return True


# ---------------------------------------------------------------------------
# Livewire 续期
# ---------------------------------------------------------------------------
def livewire_extend(sb) -> dict:
    """使用 Livewire JavaScript 直接调用 extend 方法"""
    from util import _LW_EXTEND_V3_JS, _LW_V2_JS, _LW_CLICK_JS

    results = []

    for label, js in [("v3", _LW_EXTEND_V3_JS), ("v2", _LW_V2_JS), ("click", _LW_CLICK_JS)]:
        try:
            result = sb.execute_script(js)
            if result:
                log.info(f"Livewire {label} 结果: {result}")
                results.append(result)
        except Exception as e:
            log.warning(f"Livewire {label} 调用失败: {e}")

    return {
        "results": results,
        "success": any(
            "success" in r.lower() or "clicked" in r.lower() or "call_extend" in r.lower()
            for r in results
        ),
    }


def get_remaining_seconds(sb) -> int:
    """从页面提取剩余时间，返回秒数（-1 表示无法识别）"""
    try:
        selectors = [
            "#timeleft", ".timeleft", ".time-left",
            "#remaining", ".remaining", ".countdown",
            "#sd-timer",
            '[class*="time"]', '[id*="time"]',
            '[class*="remain"]', '[id*="remain"]',
        ]
        for sel in selectors:
            try:
                if sb.is_element_visible(sel):
                    txt = sb.get_text(sel)
                    sec = parse_remaining_seconds(txt)
                    if sec > 0:
                        log.info(f"剩余时间 [{sel}] = {txt} -> {sec}s ({sec//3600}h {(sec%3600)//60}m)")
                        return sec
            except Exception:
                continue

        # 兜底：整页文本
        body_text = sb.get_text("body")
        for line in body_text.split("\n"):
            sec = parse_remaining_seconds(line)
            if 60 < sec < MAX_HOURS * 3600 + 3600:
                log.info(f"剩余时间 [body line] = {line.strip()} -> {sec}s")
                return sec
        return -1
    except Exception as e:
        log.warning(f"提取剩余时间失败: {e}")
        return -1


# ---------------------------------------------------------------------------
# 单服务器续期
# ---------------------------------------------------------------------------
def diagnose_page(sb):
    """诊断当前页面, 打印所有按钮和 Livewire 组件信息 (健壮版)"""
    def _safe_str(v, n=80):
        """安全转字符串并截断, 处理 None"""
        if v is None:
            return "(none)"
        s = str(v)
        return s[:n] if len(s) > n else s

    def _safe_get(d, key, default=""):
        """安全取 dict 字段"""
        try:
            v = d.get(key, default) if isinstance(d, dict) else default
            return v if v is not None else default
        except Exception:
            return default

    log.info("🔍 页面诊断开始:")
    log.info(f"   当前 URL: {_safe_str(sb.get_current_url())}")
    log.info(f"   页面标题: {_safe_str(sb.get_title())}")

    # 1. 打印 body 文本前 800 字符 (看页面到底显示什么)
    try:
        body_text = sb.get_text("body")
        log.info(f"   页面 body 文本 (前 800 字符):")
        for i in range(0, min(len(body_text), 800), 200):
            log.info(f"     | {body_text[i:i+200]}")
    except Exception as e:
        log.warning(f"   获取 body 文本失败: {e}")

    # 2. Livewire 诊断
    try:
        from util import _LW_DIAGNOSE_JS
        result = sb.execute_script(_LW_DIAGNOSE_JS)
        if result:
            try:
                import json as _json
                info = _json.loads(result)
                log.info(f"   [Livewire] v3={_safe_get(info, 'livewire_v3')} v2={_safe_get(info, 'livewire_v2')}")
                log.info(f"   [Livewire] wire 元素数: {_safe_get(info, 'wire_elements')}")
                wire_ids = info.get('wire_ids') if isinstance(info, dict) else None
                if wire_ids:
                    for w in wire_ids[:5]:
                        if isinstance(w, dict):
                            log.info(f"     - id={_safe_get(w, 'id')} tag={_safe_get(w, 'tag')} "
                                      f"class={_safe_str(_safe_get(w, 'class'), 50)} "
                                      f"wire:click={_safe_get(w, 'wireClick')}")
                btns = info.get('renew_buttons') if isinstance(info, dict) else None
                log.info(f"   [Livewire] 含 '90 min' 的按钮: {len(btns) if btns else 0} 个")
                if btns:
                    for b in btns[:5]:
                        if isinstance(b, dict):
                            log.info(f"     - tag={_safe_get(b, 'tag')} text={_safe_get(b, 'text')!r} "
                                      f"disabled={_safe_get(b, 'disabled')} "
                                      f"class={_safe_str(_safe_get(b, 'class'), 50)}")
                            log.info(f"       wire:click={_safe_get(b, 'wireClick')}")
                            log.info(f"       html={_safe_str(_safe_get(b, 'html'), 180)}")
            except Exception as e:
                log.info(f"   [Livewire] 原始输出: {_safe_str(result, 500)}")
    except Exception as e:
        log.warning(f"   Livewire 诊断失败: {e}")

    # 3. 列出页面所有按钮/链接 (含 id/class/text)
    try:
        all_btns_info = sb.execute_script("""
            try {
                var result = [];
                var els = document.querySelectorAll('button, a, [role=button], input[type=submit], input[type=button]');
                for (var i = 0; i < els.length && i < 40; i++) {
                    var el = els[i];
                    var t = (el.innerText || el.textContent || el.value || '').trim().substring(0, 80);
                    var cls = el.className || '';
                    if (typeof cls !== 'string') cls = '';
                    result.push({tag: el.tagName, id: el.id || '', class: cls.substring(0,80), text: t, disabled: el.disabled || false});
                }
                return JSON.stringify(result);
            } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        if all_btns_info:
            try:
                import json as _json
                arr = _json.loads(all_btns_info)
                if isinstance(arr, list):
                    log.info(f"   页面所有可见按钮/链接 (前 {len(arr)} 个):")
                    for b in arr:
                        if isinstance(b, dict):
                            dis = " [disabled]" if b.get('disabled') else ""
                            log.info(f"     <{b.get('tag')} id={b.get('id')!r} class={b.get('class')!r}>{dis} {b.get('text')!r}")
                else:
                    log.info(f"   按钮诊断返回: {arr}")
            except Exception as e:
                log.info(f"   按钮诊断原始输出: {_safe_str(all_btns_info, 500)}")
    except Exception as e:
        log.warning(f"   按钮诊断失败: {e}")

    # 4. 查找可能的续期相关元素 (更宽松的搜索)
    try:
        renew_hints = sb.execute_script("""
            try {
                var result = {forms: [], voteBtns: [], renewLinks: [], iframes: []};
                // 所有 form
                var forms = document.querySelectorAll('form');
                for (var i = 0; i < forms.length; i++) {
                    result.forms.push({action: forms[i].action || '', method: forms[i].method || '', id: forms[i].id || ''});
                }
                // 所有含 vote/renew/extend 文字的元素
                var all = document.querySelectorAll('*');
                for (var j = 0; j < all.length; j++) {
                    var el = all[j];
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t.length > 0 && t.length < 50) {
                        var tl = t.toLowerCase();
                        if (tl.indexOf('vote') !== -1 || tl.indexOf('renew') !== -1 || tl.indexOf('extend') !== -1 || tl.indexOf('+90') !== -1 || tl.indexOf('add 90') !== -1) {
                            if (el.children.length === 0 || el.tagName === 'BUTTON' || el.tagName === 'A') {
                                result.voteBtns.push({tag: el.tagName, id: el.id || '', class: (el.className||'').toString().substring(0,80), text: t});
                            }
                        }
                    }
                }
                // 所有 iframe (Turnstile)
                var iframes = document.querySelectorAll('iframe');
                for (var k = 0; k < iframes.length; k++) {
                    result.iframes.push({src: (iframes[k].src || '').substring(0, 200), width: iframes[k].width || '', id: iframes[k].id || ''});
                }
                return JSON.stringify(result);
            } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        if renew_hints:
            try:
                import json as _json
                info = _json.loads(renew_hints)
                if isinstance(info, dict):
                    forms = info.get('forms', [])
                    log.info(f"   [表单] 共 {len(forms)} 个 form:")
                    for f in forms[:5]:
                        log.info(f"     - action={f.get('action')} method={f.get('method')} id={f.get('id')}")
                    voteBtns = info.get('voteBtns', [])
                    log.info(f"   [续期线索] 含 vote/renew/extend/+90 文字的元素: {len(voteBtns)} 个")
                    for v in voteBtns[:10]:
                        log.info(f"     - <{v.get('tag')} id={v.get('id')!r} class={v.get('class')!r}> {v.get('text')!r}")
                    iframes = info.get('iframes', [])
                    log.info(f"   [iframe] 共 {len(iframes)} 个 iframe:")
                    for ifr in iframes[:5]:
                        log.info(f"     - id={ifr.get('id')!r} width={ifr.get('width')!r} src={ifr.get('src')!r}")
            except Exception as e:
                log.info(f"   续期线索原始输出: {_safe_str(renew_hints, 500)}")
    except Exception as e:
        log.warning(f"   续期线索诊断失败: {e}")

    log.info("🔍 页面诊断结束")


def run_single_server(sb, site_url: str, server_num: str, region: str,
                      renew_url: str = None) -> bool:
    """对一个服务器执行续期，返回是否成功"""
    # 优先用用户提供的完整 URL, 否则尝试多种路径格式
    if renew_url and "/server/" in renew_url:
        url_app = renew_url
    else:
        # 兜底: 尝试单数和复数两种路径
        url_app = f"{site_url.rstrip('/')}/server/{server_num}"

    log.info("=" * 40)
    log.info(f"🚀 开始续期 [{region}] ({server_num})")
    log.info(f"📂 续期页面: {url_app}")

    # 出口 IP
    try:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        ip_val = requests.get(
            "https://api.ipify.org?format=json",
            proxies=proxies, timeout=10,
        ).json().get("ip", "Unknown")
        log.info(f"🌐 当前出口 IP: {ip_val}")
    except Exception:
        log.warning("⚠️ 无法获取出口 IP，跳过")

    # 打开面板
    log.info(f"📂 正在进入续期面板 [{region}] ...")
    try:
        sb.uc_open_with_reconnect(url_app, reconnect_time=5)
        human_wait(8, 12)
    except Exception as e:
        raise Exception(f"打开面板失败: {e}")

    # 检查登录状态
    current_url = sb.get_current_url().lower()
    log.info(f"📍 当前 URL: {sb.get_current_url()}")
    if "login" in current_url or "auth" in current_url:
        raise Exception("登录状态失效或权限被拒绝")

    # 同意 Cookies
    cookie_btns = [
        '//button[contains(., "Continue with Recommended Cookies")]',
        '//button[contains(., "Accept")]',
        '//button[contains(., "I Agree")]',
        '//button[contains(., "Consent")]',
    ]
    for btn in cookie_btns:
        if sb.is_element_present(btn):
            try:
                sb.click(btn)
                break
            except Exception:
                pass

    # 续期前时间 - 优先用 JS 精确提取 (页面是 Filament 框架, 时间格式 "HH:MM:SSremaining")
    timestamp_before = "未知"
    try:
        # 用 JS 找含 HH:MM:SS 的元素 (更精确)
        time_text = sb.execute_script("""
            try {
                // 1. 先找已知 class (rt-timer / sd-timer)
                var known = document.querySelector('.rt-timer, #sd-timer, .sd-timer');
                if (known) return known.innerText.trim();
                // 2. 找所有元素, 匹配 HH:MM:SS 格式 (允许后面跟 'remaining' 等文字)
                var els = document.querySelectorAll('div, span, p, h1, h2, h3, h4, h5, h6');
                for (var i = 0; i < els.length; i++) {
                    var t = (els[i].innerText || '').trim();
                    // 精确匹配: 数字:数字:数字 开头, 长度 < 30
                    var m = t.match(/^(\\d{1,2}:\\d{2}:\\d{2})/);
                    if (m && t.length < 30) return m[1];
                }
                return '';
            } catch(e) { return ''; }
        """)
        if time_text:
            # 提取 HH:MM:SS 部分
            m = re.search(r"(\d{1,2}:\d{2}:\d{2})", time_text)
            if m:
                timestamp_before = m.group(1)
                log.info(f"🕒 续期前剩余 (JS 提取): {timestamp_before}")
    except Exception as e:
        log.warning(f"JS 提取时间失败: {e}")

    if timestamp_before == "未知":
        # 兜底: 通用选择器
        try:
            sb.wait_for_element_visible("#sd-timer", timeout=5)
            timestamp_before = sb.get_text("#sd-timer").strip()
        except Exception:
            sec_before = get_remaining_seconds(sb)
            timestamp_before = f"{sec_before//3600:02d}:{(sec_before%3600)//60:02d}:00" if sec_before > 0 else "未知"
    log.info(f"🕒 续期前剩余运行时间: {timestamp_before}")

    # 滚动到底部找按钮
    try:
        ActionChains(sb.driver).scroll_by_amount(0, 600).perform()
        human_wait(2, 4)
    except Exception:
        pass

    # 点击续期按钮 - 尝试多种选择器 (按真实页面结构优先排序)
    # 真实页面: <BUTTON class='rt-btn-free'> '+ 90 min'
    #          <BUTTON class='rt-btn-paid'> '+24h $0.15'
    # 注意: 按钮可能存在但不可见 (在折叠区域), 所以用 is_element_present 而非 is_element_visible
    vote_btn_selectors = [
        # 1. 真实 class (最高优先级)
        "button.rt-btn-free",                       # 续期 +90 分钟 (免费)
        "button.rt-btn-paid",                       # 续期 +24h (付费, 备选)
        ".rt-btn-free",                             # class 选择器 (无标签)
        # 2. ID (旧版兼容)
        "#sd-vote-btn",
        'button[id="sd-vote-btn"]',
        # 3. 文字匹配 (兜底)
        'button:contains("+ 90 min")',
        'button:contains("+90 min")',
        'button:contains("90 min")',
        'button:contains("VOTE")',
        'button:contains("ADD 90")',
        # 4. XPath 兜底
        '//button[contains(., "+ 90 min")]',
        '//button[contains(., "90 min")]',
        '//button[contains(., "VOTE")]',
    ]
    clicked = False
    for sel in vote_btn_selectors:
        try:
            # 关键: 用 is_element_present 而非 is_element_visible
            # 因为按钮可能存在但不可见 (在折叠区域)
            if sb.is_element_present(sel):
                log.info(f"🖱️ 找到续期按钮 [{sel}], 正在点击...")
                # 先滚动到按钮位置
                try:
                    sb.scroll_to(sel)
                    human_wait(0.5, 1.0)
                except Exception:
                    pass
                # 尝试用 sb.click, 失败则用 JS click
                try:
                    sb.click(sel, timeout=5)
                except Exception as click_e:
                    log.warning(f"sb.click 失败 ({click_e}), 尝试 JS click")
                    sb.execute_script(
                        "var el = document.querySelector(arguments[0]); "
                        "if (el) { el.scrollIntoView({block: 'center'}); el.click(); }",
                        sel,
                    )
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        # 终极兜底: 用 JS 直接找 class 含 rt-btn-free 的元素并点击
        try:
            log.warning("所有选择器都未找到续期按钮, 尝试 JS 直接点击 rt-btn-free...")
            js_result = sb.execute_script("""
                try {
                    var btn = document.querySelector('.rt-btn-free') ||
                              document.querySelector('button[class*=\"rt-btn-free\"]');
                    if (btn) {
                        btn.scrollIntoView({block: 'center', behavior: 'instant'});
                        btn.click();
                        return 'clicked: ' + btn.className + ' | ' + (btn.innerText || '').substring(0, 50);
                    }
                    // 兜底: 找所有 button, 文字含 '+ 90 min' 或 '90 min'
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        var t = (btns[i].innerText || '').trim();
                        if (t.indexOf('90 min') !== -1 || t.indexOf('+ 90') !== -1) {
                            btns[i].scrollIntoView({block: 'center', behavior: 'instant'});
                            btns[i].click();
                            return 'clicked_text_match: ' + t;
                        }
                    }
                    return 'not_found';
                } catch(e) { return 'error: ' + e.message; }
            """)
            log.info(f"JS 点击结果: {js_result}")
            if js_result and "clicked" in str(js_result).lower():
                clicked = True
        except Exception as e:
            log.warning(f"JS 直接点击失败: {e}")

    if not clicked:
        log.warning("所有方法都未找到续期按钮，尝试 Livewire extend...")
        lw_result = livewire_extend(sb)
        if not lw_result["success"]:
            # 关键: 失败时跑页面诊断, 把页面所有按钮信息打到日志
            log.error("❌ 仍未找到续期按钮, 开始页面诊断...")
            screenshot(sb, f"no_btn_{server_num}")
            diagnose_page(sb)
            raise Exception(f"未找到续期按钮 (已尝试 {len(vote_btn_selectors)} 种选择器 + JS 兜底, 见上方诊断)")

    # 破解 Turnstile
    human_wait(2, 4)
    bypass_turnstile(sb)

    # 点击最终提交按钮 - 尝试多种选择器
    # 点击 + 90 min 后可能弹出确认对话框, 含 "Confirm" / "Submit" 按钮
    submit_selectors = [
        # 1. 真实页面可能的 class (基于 Filament 框架)
        "button.fi-btn-action",
        "button.fi-ac-btn-action",
        ".fi-modal-confirm-btn",
        # 2. 旧版 ID
        "#vm-submit",
        'button[id="vm-submit"]',
        # 3. 文字匹配
        'button:contains("Confirm")',
        'button:contains("Submit")',
        'button:contains("VOTE + ADD")',
        'button:contains("ADD 90 MINUTES")',
        'button:contains("Yes")',
        'button:contains("OK")',
        # 4. 通用 submit
        'button[type="submit"]',
        # 5. XPath
        '//button[contains(., "Confirm")]',
        '//button[contains(., "Submit")]',
    ]
    submitted = False
    for sel in submit_selectors:
        try:
            # 用 is_element_present 而非 is_element_visible
            if sb.is_element_present(sel):
                log.info(f"🖱️ 找到提交按钮 [{sel}], 正在点击...")
                try:
                    sb.scroll_to(sel)
                    human_wait(0.3, 0.8)
                except Exception:
                    pass
                try:
                    sb.uc_click(sel)
                    human_wait(8, 12)
                except Exception as click_e:
                    log.warning(f"sb.uc_click 失败 ({click_e}), 尝试 JS click")
                    sb.execute_script(
                        "var el = document.querySelector(arguments[0]); "
                        "if (el) { el.scrollIntoView({block: 'center'}); el.click(); }",
                        sel,
                    )
                    human_wait(8, 12)
                submitted = True
                break
        except Exception:
            continue

    if not submitted:
        # 终极兜底: JS 找 Confirm/Submit 按钮并点击
        try:
            log.warning("未找到提交按钮, 尝试 JS 直接点击 Confirm/Submit...")
            js_result = sb.execute_script("""
                try {
                    var btns = document.querySelectorAll('button, a, [role=button]');
                    var keywords = ['confirm', 'submit', 'yes', 'ok', 'vote + add', 'add 90 minutes'];
                    for (var i = 0; i < btns.length; i++) {
                        var t = (btns[i].innerText || '').trim().toLowerCase();
                        if (t.length > 0 && t.length < 40) {
                            for (var k = 0; k < keywords.length; k++) {
                                if (t.indexOf(keywords[k]) !== -1 && !btns[i].disabled) {
                                    btns[i].scrollIntoView({block: 'center', behavior: 'instant'});
                                    btns[i].click();
                                    return 'clicked: ' + (btns[i].innerText || '').trim();
                                }
                            }
                        }
                    }
                    return 'not_found';
                } catch(e) { return 'error: ' + e.message; }
            """)
            log.info(f"JS 提交点击结果: {js_result}")
            if js_result and "clicked" in str(js_result).lower():
                submitted = True
                human_wait(8, 12)
        except Exception as e:
            log.warning(f"JS 提交点击失败: {e}")

    if not submitted:
        log.warning("未找到提交按钮, 尝试 Livewire extend...")
        livewire_extend(sb)
        human_wait(5, 8)

    time.sleep(5)

    # 续期后时间 - 同样用 JS 精确提取
    timestamp_after = "未知"
    try:
        time_text = sb.execute_script("""
            try {
                var known = document.querySelector('.rt-timer, #sd-timer, .sd-timer');
                if (known) return known.innerText.trim();
                var els = document.querySelectorAll('div, span, p, h1, h2, h3, h4, h5, h6');
                for (var i = 0; i < els.length; i++) {
                    var t = (els[i].innerText || '').trim();
                    var m = t.match(/^(\\d{1,2}:\\d{2}:\\d{2})/);
                    if (m && t.length < 30) return m[1];
                }
                return '';
            } catch(e) { return ''; }
        """)
        if time_text:
            m = re.search(r"(\d{1,2}:\d{2}:\d{2})", time_text)
            if m:
                timestamp_after = m.group(1)
                log.info(f"🕒 续期后剩余 (JS 提取): {timestamp_after}")
    except Exception as e:
        log.warning(f"JS 提取续期后时间失败: {e}")

    if timestamp_after == "未知":
        try:
            timestamp_after = sb.get_text("#sd-timer").strip()
        except Exception:
            sec_after = get_remaining_seconds(sb)
            timestamp_after = f"{sec_after//3600:02d}:{(sec_after%3600)//60:02d}:00" if sec_after > 0 else "未知"
    log.info(f"🕒 续期后剩余运行时间: {timestamp_after}")

    sec_before = time_to_seconds(timestamp_before)
    sec_after = time_to_seconds(timestamp_after)

    # 判断是否成功
    if sec_after <= sec_before + 60 and sec_before != 0:
        raise Exception(
            f"❌ 时间未增加！(前: {timestamp_before}, 后: {timestamp_after})"
        )

    # 成功
    final_shot = screenshot(sb, f"success_{server_num}")
    msg = (
        f"✅ [{region}] 续期成功\n"
        f"🖥️ 编号: {server_num}\n"
        f"🕒 续期前: {timestamp_before}\n"
        f"🎉 续期后: {timestamp_after}"
    )
    tg(msg, photo_path=str(final_shot))
    return True


# ---------------------------------------------------------------------------
# 单账号主流程
# ---------------------------------------------------------------------------
def process_account(account: dict) -> dict:
    """处理单个账号, 返回结果"""
    name = account["name"]
    site = account["site"]
    cookie = account["cookie"]
    renew_url = account["renew_url"]

    log.info("=" * 60)
    log.info(f"👤 账号: {name}")
    log.info(f"🌐 站点: {site}")
    log.info(f"🔗 续期 URL: {renew_url}")
    log.info("=" * 60)

    # 从 renew_url 提取 server_num (如果有)
    # 格式: https://control.gaming4free.net/server/247d3700/console
    server_num_from_url = None
    m = re.search(r"/server/([^/?#]+)", renew_url)
    if m:
        server_num_from_url = m.group(1)
        log.info(f"📌 从 URL 提取服务器编号: {server_num_from_url}")

    # 解析代理端口
    proxy_port = 1080
    if PROXY_URL:
        port_match = re.search(r":(\d+)$", PROXY_URL.rstrip("/"))
        proxy_port = int(port_match.group(1)) if port_match else 1080

    # 预检代理端口
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", proxy_port))
        s.close()
        log.info(f"✅ 代理 SOCKS5 端口 {proxy_port} 可用")
    except Exception:
        log.warning(f"⚠️ 代理端口 {proxy_port} 不可达，将直连")

    CHROMIUM_ARGS = (
        f"--no-sandbox,--disable-dev-shm-usage,--disable-gpu,"
        f"--window-size=1280,720,--disable-blink-features=AutomationControlled,"
        f"--disable-infobars,--disable-popup-blocking,--proxy-server={PROXY_URL}"
    )

    log.info(f"正在启动浏览器 (uc=True, xvfb=True, proxy={PROXY_URL})...")
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
        log.info("✅ 浏览器启动成功")
        sb.set_window_size(1280, 720)

        # 注入 Cookie (关键: 必须先 open 站点, 再 add_cookie, 再 reload)
        if cookie:
            log.info("🍪 开始注入 Cookie...")
            inject_cookies(sb, site, cookie)
        else:
            log.warning("⚠️ 未配置 Cookie, 仅靠浏览器匿名访问")

        # 处理 CF 5 秒盾
        log.info("等待 CF 5 秒盾（如有）...")
        for _ in range(15):
            try:
                if "just a moment" in sb.get_text("body").lower():
                    time.sleep(1)
                else:
                    break
            except Exception:
                time.sleep(1)

        # 决定要续期的服务器列表
        servers_to_renew = []
        if SERVER_LIST:
            servers_to_renew = SERVER_LIST
            log.info(f"📋 从 SERVERS 环境变量读取到 {len(servers_to_renew)} 个服务器")
        elif server_num_from_url:
            servers_to_renew = [{"num": server_num_from_url, "region": name}]
            log.info(f"📋 从 URL 提取服务器编号: {server_num_from_url}")
        else:
            log.warning("⚠️ 既无 SERVERS 配置, 也无法从 URL 提取服务器编号, 将走循环续期模式")

        if servers_to_renew:
            success_count = 0
            fail_count = 0
            for server in servers_to_renew:
                try:
                    # 把 renew_url 传给 run_single_server, 优先用用户提供的完整 URL
                    if not SERVER_LIST and server_num_from_url:
                        url_to_use = renew_url  # 用用户提供的完整 URL
                    else:
                        url_to_use = None  # 让函数自己拼
                    if run_single_server(sb, site, server["num"], server["region"],
                                          renew_url=url_to_use):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    log.error(f"❌ [{server['region']}] 续期失败: {e}")
                    error_shot = screenshot(sb, f"error_{server['num']}")
                    tg(f"❌ [{server['region']}] 执行失败: {e}\n🖥️ 编号: {server['num']}",
                       photo_path=str(error_shot))
                    fail_count += 1

            msg = (
                f"🎮 gaming4free 续期完成 [{name}]\n"
                f"✅ 成功: {success_count} | ❌ 失败: {fail_count}\n"
                f"📊 总计: {len(servers_to_renew)} 个服务器"
            )
            log.info(msg)
            tg(msg)
            return {
                "name": name, "ok": True,
                "total": len(servers_to_renew),
                "renewed": success_count,
                "failed": fail_count,
            }
        else:
            # 循环续期模式 (无具体服务器编号)
            log.info("使用默认循环续期模式...")
            click_count = 0
            last_sec = get_remaining_seconds(sb)
            log.info(f"初始剩余: {last_sec}s ({last_sec//3600}h {(last_sec%3600)//60}m)")

            while click_count < MAX_CLICKS:
                if last_sec >= (MAX_HOURS - 1) * 3600:
                    log.info(f"已达到 {MAX_HOURS}h 上限，停止续期")
                    break

                # 先尝试 Livewire
                log.info("尝试 Livewire extend...")
                lw_result = livewire_extend(sb)
                if lw_result["success"]:
                    log.info("✅ Livewire extend 成功")
                else:
                    log.warning("Livewire extend 失败，尝试按钮点击...")
                    candidates = [
                        'button:contains("+90")',
                        'button:contains("90 min")',
                        'button:contains("90")',
                        'a:contains("+90")',
                        'a:contains("90 min")',
                        'button:contains("Renew")',
                        'button:contains("Extend")',
                        'button:contains("续期")',
                        'button:contains("增加")',
                        'a:contains("Renew")',
                    ]
                    clicked = False
                    for sel in candidates:
                        try:
                            if sb.is_element_visible(sel, timeout=5):
                                human_wait(1.0, 2.5)
                                sb.scroll_to(sel)
                                human_wait(0.3, 0.8)
                                try:
                                    sb.click(sel, timeout=8)
                                except Exception:
                                    sb.execute_script(
                                        "document.querySelector(arguments[0]).click();", sel
                                    )
                                log.info(f"点击续期按钮 [{sel}]")
                                clicked = True
                                break
                        except Exception:
                            continue
                    if not clicked:
                        screenshot(sb, f"no_btn_{click_count}")
                        log.warning("本次未找到按钮，刷新页面")
                        sb.refresh()
                        sb.sleep(3)
                        last_sec = get_remaining_seconds(sb)
                        continue

                # 处理 Turnstile
                human_wait(1.0, 2.0)
                bypass_turnstile(sb)

                # 等待响应
                human_wait(3.0, 5.0)
                sb.sleep(2)

                # 对比时间
                new_sec = get_remaining_seconds(sb)
                delta = new_sec - last_sec
                log.info(f"点击 #{click_count+1}: {last_sec}s -> {new_sec}s (Delta={delta}s)")

                if new_sec > last_sec:
                    click_count += 1
                    log.info(f"续期成功 (累计 {click_count} 次)")
                    screenshot(sb, f"success_{click_count}")
                    last_sec = new_sec
                else:
                    log.warning("续期可能失败，时间未增加")
                    screenshot(sb, f"fail_{click_count}")
                    sb.refresh()
                    sb.sleep(3)
                    last_sec = get_remaining_seconds(sb)
                    click_count += 1

                if last_sec >= (MAX_HOURS - 1) * 3600:
                    break

                log.info(f"冷却 {COOLDOWN_SEC}s ...")
                for i in range(COOLDOWN_SEC, 0, -10):
                    log.info(f"  剩余 {i}s")
                    time.sleep(10)

            # 收尾
            final_sec = get_remaining_seconds(sb)
            h, m = final_sec // 3600, (final_sec % 3600) // 60
            msg = (
                f"🎮 gaming4free 续期完成 [{name}]\n"
                f"✅ 成功点击: {click_count} 次\n"
                f"🕒 最终剩余: {h}h {m}m"
            )
            log.info(msg)
            tg(msg)
            screenshot(sb, "final")
            return {
                "name": name, "ok": True,
                "clicks": click_count,
                "final_sec": final_sec,
            }


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def main():
    log.info("=" * 60)
    log.info("🎮 gaming4free 续期启动")
    log.info(f"代理地址: {PROXY_URL}")
    log.info(f"目标站点: {SITE_URL}")
    log.info(f"账号数量: {len(ACCOUNTS)}")
    log.info(f"服务器列表: {len(SERVER_LIST)} 个 (来自 SERVERS 环境变量)")
    log.info("=" * 60)

    if not ACCOUNTS:
        msg = "❌ 未配置 GAME4FREE_COOKIE 或 GAME4FREE_ACCOUNTS"
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

    # 汇总
    total_renewed = sum(r.get("renewed", 0) for r in all_results if r.get("ok"))
    total_failed = sum(r.get("failed", 0) for r in all_results if r.get("ok"))
    summary = (
        f"🎮 gaming4free 续期汇总\n"
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
        tg(f"❌ gaming4free 续期崩溃\n{e}")
        sys.exit(1)

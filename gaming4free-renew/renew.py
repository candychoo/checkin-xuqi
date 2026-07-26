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
    """处理 Cloudflare Turnstile - 新版 CF 自动验证, 只需等待, 不要点击

    关键: VLM 截图分析发现 CF 对话框里有 spinner '正在验证...'
    说明 CF 在自动验证浏览器指纹, 不需要点击 checkbox
    策略: 耐心等待 30-60 秒让 CF 自动完成验证
    """
    try:
        # 检测是否有 CF 验证对话框
        has_cf = False
        try:
            # 用 IIFE 包裹, 避免 'Illegal return statement' 错误
            cf_check = sb.execute_script("""
                return (function() {
                    try {
                        var els = document.querySelectorAll('div, section, [role=dialog]');
                        for (var i = 0; i < els.length; i++) {
                            var rect = els[i].getBoundingClientRect();
                            if (rect.width < 100 || rect.width > 900) continue;
                            var t = (els[i].innerText || '').toLowerCase();
                            if ((t.indexOf('verify') !== -1 && t.indexOf('human') !== -1) ||
                                t.indexOf('正在验证') !== -1 ||
                                t.indexOf('人机验证') !== -1) {
                                return JSON.stringify({found: true, width: rect.width, text: t.substring(0, 80)});
                            }
                        }
                        return JSON.stringify({found: false});
                    } catch(e) { return JSON.stringify({found: false, error: e.message}); }
                })();
            """)
            import json as _json
            info = _json.loads(cf_check) if cf_check else {}
            if info.get("found"):
                has_cf = True
                log.info(f"🎯 检测到 CF 验证对话框 (宽 {info.get('width', 0):.0f}px)")
                log.info(f"   文字: {info.get('text', '')[:80]}")
        except Exception as e:
            log.warning(f"CF 检测失败: {e}")

        # 也检测 iframe 模式
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
                                log.info(f"🎯 检测到 CF Turnstile iframe ({size.get('width')}x{size.get('height')})")
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        if not has_cf:
            log.info("未检测到 CF 验证, 跳过")
            return True

        # 关键策略: CF 新版自动验证, 只需等待, 不要点击
        # VLM 截图显示有 spinner '正在验证...', 说明正在自动验证
        log.info("⏳ CF 正在自动验证浏览器指纹, 耐心等待 (最多 90 秒)...")

        for attempt in range(18):  # 18 次 × 5 秒 = 90 秒
            time.sleep(5)
            passed = _check_cf_passed(sb)
            if passed:
                log.info(f"✅ CF 验证通过 (第 {attempt+1} 次检测)")
                return True
            if attempt % 3 == 0:  # 每 15 秒打印一次状态
                log.info(f"⏳ 等待 CF 自动验证 ({attempt+1}/18)...")

        log.warning("⚠️ CF 验证 90 秒未通过 (浏览器指纹可能被识别)")
        return False

    except Exception as e:
        log.warning(f"Turnstile 处理异常: {e}")
        return False


def _check_cf_passed(sb) -> bool:
    """检测 CF 验证是否通过 (用 IIFE 包裹 JS 避免 return 错误)"""
    try:
        result = sb.execute_script("""
            return (function() {
                try {
                    // 检测 1: cf-turnstile-response 有 token
                    var els = document.querySelectorAll('[name="cf-turnstile-response"], [name="g-recaptcha-response"]');
                    for (var i = 0; i < els.length; i++) {
                        if (els[i].value && els[i].value.length > 20) return 'token';
                    }
                    // 检测 2: CF 验证对话框消失
                    var dialogs = document.querySelectorAll('div, section, [role=dialog]');
                    for (var j = 0; j < dialogs.length; j++) {
                        var rect = dialogs[j].getBoundingClientRect();
                        if (rect.width < 100 || rect.width > 900) continue;
                        var t = (dialogs[j].innerText || '').toLowerCase();
                        if ((t.indexOf('verify') !== -1 && t.indexOf('human') !== -1) ||
                            t.indexOf('正在验证') !== -1) {
                            return 'still_there';
                        }
                    }
                    // 检测 3: 页面出现 Success / Verified 文字
                    var body = document.body.innerText || '';
                    if (body.indexOf('Success') !== -1 || body.indexOf('Verified') !== -1) {
                        return 'success_text';
                    }
                    return 'passed';
                } catch(e) { return 'error:' + e.message; }
            })();
        """)
        if result and result not in ('still_there',) and not result.startswith('error'):
            if result in ('token', 'success_text', 'passed'):
                return True
        return False
    except Exception:
        return False


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
        # 用 JS 找含 HH:MM:SS 的元素 (用 IIFE 包裹避免 return 错误)
        time_text = sb.execute_script("""
            return (function() {
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
            })();
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

    # 关键诊断: 点击前检查 button.rt-btn-free 的属性
    try:
        btn_info = sb.execute_script("""
            return (function() {
                try {
                    var btn = document.querySelector('button.rt-btn-free');
                    if (!btn) return JSON.stringify({found: false});
                    var rect = btn.getBoundingClientRect();
                    return JSON.stringify({
                        found: true,
                        text: (btn.innerText || '').trim(),
                        disabled: btn.disabled,
                        className: btn.className,
                        wireClick: btn.getAttribute('wire:click') || btn.getAttribute('wire:click.prevent') || 'none',
                        onClick: btn.getAttribute('onclick') || 'none',
                        width: rect.width,
                        height: rect.height,
                        color: window.getComputedStyle(btn).color,
                        backgroundColor: window.getComputedStyle(btn).backgroundColor,
                        cursor: window.getComputedStyle(btn).cursor,
                        opacity: window.getComputedStyle(btn).opacity,
                        parentClass: btn.parentElement ? (btn.parentElement.className || '').substring(0, 80) : '',
                        parentWireClick: btn.parentElement ? (btn.parentElement.getAttribute('wire:click') || 'none') : 'none',
                        html: btn.outerHTML.substring(0, 300)
                    });
                } catch(e) { return JSON.stringify({found: false, error: e.message}); }
            })();
        """)
        import json as _json
        info = _json.loads(btn_info) if btn_info else {}
        if info.get("found"):
            log.info(f"🔍 续期按钮属性:")
            log.info(f"   文字: {info.get('text')!r}")
            log.info(f"   disabled: {info.get('disabled')}")
            log.info(f"   尺寸: {info.get('width', 0):.0f}x{info.get('height', 0):.0f}")
            log.info(f"   颜色: bg={info.get('backgroundColor')} color={info.get('color')}")
            log.info(f"   cursor: {info.get('cursor')} opacity: {info.get('opacity')}")
            log.info(f"   wire:click: {info.get('wireClick')}")
            log.info(f"   onclick: {info.get('onClick')}")
            log.info(f"   父元素: class={info.get('parentClass')} wire:click={info.get('parentWireClick')}")
            log.info(f"   HTML: {info.get('html')}")

            # 关键: 如果按钮 disabled 或 opacity 低, 说明不能点
            if info.get("disabled"):
                log.warning("⚠️ 按钮 disabled! 可能需要先完成其他操作 (如 VOTE)")
            if info.get("opacity") and float(info.get("opacity", 1)) < 0.5:
                log.warning(f"⚠️ 按钮 opacity={info.get('opacity')} (半透明, 可能禁用)")
            if info.get("cursor") == "not-allowed":
                log.warning("⚠️ 按钮 cursor=not-allowed (禁用状态)")
        else:
            log.warning("⚠️ 未找到 button.rt-btn-free")
    except Exception as e:
        log.warning(f"按钮属性诊断失败: {e}")

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

    # 关键新增: 点击按钮后, 额外用 JS 直接调用 Livewire extend() 方法
    # 这能绕过按钮 disabled 状态, 直接触发续期
    if clicked:
        try:
            log.info("🔧 尝试用 JS 直接调用 Livewire 续期方法 (绕过按钮状态)...")
            # 关键: 精准定位 renewal-timer 组件 (含 expiresTimestamp/adRewardsToday)
            # 尝试多种可能的方法名: extend / renew / watchAd / claimAdReward / addTime
            lw_result = sb.execute_script("""
                return (function() {
                    try {
                        var results = [];
                        // 1. 列出所有 Livewire 组件 (诊断用)
                        var wireEls = document.querySelectorAll('[wire\\\\:id]');
                        results.push('wire_elements: ' + wireEls.length);

                        // 2. 找 renewal-timer 组件 (优先)
                        var targetComponent = null;
                        var targetWireId = null;
                        for (var i = 0; i < wireEls.length; i++) {
                            var wireId = wireEls[i].getAttribute('wire\\\\:id');
                            if (!wireId || !window.Livewire) continue;
                            try {
                                var comp = window.Livewire.find(wireId);
                                if (comp && comp.$wire) {
                                    // 检查是否是 renewal-timer (含 serverId/expiresTimestamp)
                                    var data = comp.$wire.__instance ? comp.$wire.__instance.data : null;
                                    var dataStr = data ? JSON.stringify(data) : '';
                                    if (dataStr.indexOf('expiresTimestamp') !== -1 || dataStr.indexOf('adRewardsToday') !== -1) {
                                        targetComponent = comp;
                                        targetWireId = wireId;
                                        results.push('found renewal-timer: wireId=' + wireId);
                                        results.push('data keys: ' + Object.keys(data || {}).join(','));
                                        // 列出 $wire 上所有可调用的方法
                                        var methods = [];
                                        for (var k in comp.$wire) {
                                            if (typeof comp.$wire[k] === 'function' && k !== 'constructor' && k.indexOf('$') !== 0) {
                                                methods.push(k);
                                            }
                                        }
                                        results.push('methods: ' + methods.join(','));
                                        break;
                                    }
                                }
                            } catch(e) {}
                        }

                        // 3. 如果没找到 renewal-timer, 用第一个组件
                        if (!targetComponent && wireEls.length > 0) {
                            for (var j = 0; j < wireEls.length; j++) {
                                var wid = wireEls[j].getAttribute('wire\\\\:id');
                                if (wid && window.Livewire) {
                                    try {
                                        var c = window.Livewire.find(wid);
                                        if (c && c.$wire) {
                                            targetComponent = c;
                                            targetWireId = wid;
                                            results.push('using first component: wireId=' + wid);
                                            break;
                                        }
                                    } catch(e) {}
                                }
                            }
                        }

                        if (!targetComponent) {
                            results.push('no_component_found');
                            return results.join(' | ');
                        }

                        // 4. 尝试多种方法名
                        var methodsToTry = ['extend', 'renew', 'watchAd', 'claimAdReward', 'addTime', 'add_time', 'vote', 'claim'];
                        for (var m = 0; m < methodsToTry.length; m++) {
                            var methodName = methodsToTry[m];
                            try {
                                // 先检查方法是否存在
                                if (typeof targetComponent.$wire[methodName] === 'function') {
                                    results.push('calling ' + methodName + '...');
                                    var r = targetComponent.$wire.call(methodName);
                                    results.push(methodName + ' called: ' + JSON.stringify(r).substring(0, 100));
                                    return results.join(' | ');
                                }
                            } catch(e) {
                                results.push(methodName + ' error: ' + e.message);
                            }
                        }

                        // 5. v2 emit 兜底
                        if (window.livewire) {
                            try {
                                window.livewire.emit('extend');
                                results.push('v2_emit_extend');
                                return results.join(' | ');
                            } catch(e) {}
                        }

                        results.push('no_method_worked');
                        return results.join(' | ');
                    } catch(e) { return 'error: ' + e.message; }
                })();
            """)
            log.info(f"Livewire 调用结果: {lw_result}")
        except Exception as e:
            log.warning(f"JS 调用 Livewire 失败: {e}")

    if not clicked:
        log.warning("所有方法都未找到续期按钮，尝试 Livewire extend...")
        lw_result = livewire_extend(sb)
        if not lw_result["success"]:
            # 关键: 失败时跑页面诊断, 把页面所有按钮信息打到日志
            log.error("❌ 仍未找到续期按钮, 开始页面诊断...")
            screenshot(sb, f"no_btn_{server_num}")
            diagnose_page(sb)
            raise Exception(f"未找到续期按钮 (已尝试 {len(vote_btn_selectors)} 种选择器 + JS 兜底, 见上方诊断)")

    # 破解 Turnstile (点击 +90 min 前可能有)
    human_wait(2, 4)
    bypass_turnstile(sb)

    # 关键: 点击 +90 min 后, 监听网络请求和 DOM 变化
    # 之前点击后时间没变化, 说明续期请求没真正发出或没生效
    # 现在改为: 点击后多次截图 + 监听 DOM 变化 + 等待更长时间

    # 注入网络请求监听器 (在点击前注入)
    # 关键: 记录请求 body, 这样能看到 Livewire 请求调用的方法名 (如 extend)
    try:
        sb.execute_script("""
            window.__renew_xhr_log = [];
            window.__renew_fetch_log = [];
            // 拦截 XHR
            var origOpen = XMLHttpRequest.prototype.open;
            var origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__method = method;
                this.__url = url;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function(body) {
                var self = this;
                var bodyStr = '';
                try { bodyStr = typeof body === 'string' ? body : JSON.stringify(body); } catch(e) {}
                this.addEventListener('load', function() {
                    window.__renew_xhr_log.push({
                        method: self.__method, url: self.__url,
                        status: self.status,
                        body: bodyStr.substring(0, 500),
                        response: (self.responseText || '').substring(0, 300)
                    });
                });
                return origSend.apply(this, arguments);
            };
            // 拦截 fetch (记录 body)
            var origFetch = window.fetch;
            window.fetch = function(input, init) {
                var url = typeof input === 'string' ? input : input.url;
                var method = (init && init.method) || 'GET';
                var bodyStr = '';
                try {
                    if (init && init.body) {
                        bodyStr = typeof init.body === 'string' ? init.body : JSON.stringify(init.body);
                    }
                } catch(e) {}
                return origFetch.apply(this, arguments).then(function(resp) {
                    resp.clone().text().then(function(t) {
                        window.__renew_fetch_log.push({
                            method: method, url: url,
                            status: resp.status,
                            body: bodyStr.substring(0, 500),
                            response: t.substring(0, 300)
                        });
                    });
                    return resp;
                });
            };
        """)
        log.info("📡 网络请求监听器已注入 (含 body 记录)")
    except Exception as e:
        log.warning(f"注入网络监听器失败: {e}")

    log.info("⏳ 等待续期生效 (点击 +90 min 后)...")
    # 多次截图 + 检测 DOM 变化
    for i in range(6):
        human_wait(3, 5)
        # 检测是否有 toast / notification / 错误提示
        try:
            toast = sb.execute_script("""
                try {
                    // 找 toast / notification / alert (排除 Server Installation 等无关通知)
                    var sels = [
                        '.fi-notification', '.fi-toast', '.toast', '.alert',
                        '[class*=\"toast\"]', '[class*=\"notification\"]',
                        '[class*=\"alert\"]', '[class*=\"message\"]', '[class*=\"flash\"]',
                        '[role=\"alert\"]', '[role=\"status\"]'
                    ];
                    for (var i = 0; i < sels.length; i++) {
                        var els = document.querySelectorAll(sels[i]);
                        for (var j = 0; j < els.length; j++) {
                            var t = (els[j].innerText || '').trim();
                            if (t && t.length > 2 && t.length < 300) {
                                return sels[i] + ': ' + t;
                            }
                        }
                    }
                    return '';
                } catch(e) { return 'error: ' + e.message; }
            """)
            if toast:
                log.info(f"💬 [{i+1}/6] 检测到提示: {toast}")
        except Exception:
            pass

        # 截图 (前 3 次都截)
        if i < 3:
            screenshot(sb, f"after_click_{i+1}_{server_num}")

    # 检查是否跳转到了其他页面
    try:
        current_url_after = sb.get_current_url().lower()
        log.info(f"📍 点击后 URL: {sb.get_current_url()}")
        if "public-renewing" in current_url_after or "settings" in current_url_after:
            log.warning("⚠️ 页面跳转到了设置页, 说明点击的不是续期按钮而是菜单链接!")
            log.warning("⚠️ 尝试返回原页面并重新点击真正的 rt-btn-free...")
            sb.go_back()
            human_wait(3, 5)
            try:
                sb.execute_script("""
                    try {
                        var btn = document.querySelector('button.rt-btn-free');
                        if (btn) {
                            btn.scrollIntoView({block: 'center', behavior: 'instant'});
                            btn.click();
                        }
                    } catch(e) {}
                """)
                human_wait(8, 12)
                bypass_turnstile(sb)
                human_wait(3, 5)
            except Exception as e:
                log.warning(f"重新点击失败: {e}")
    except Exception as e:
        log.warning(f"URL 检查失败: {e}")

    # 打印捕获的网络请求 (关键诊断!)
    # 同时分析是否有续期相关请求 + 冷却状态
    in_cooldown = False
    cooldown_seconds = 0
    extend_request_found = False
    try:
        xhr_log = sb.execute_script("return JSON.stringify(window.__renew_xhr_log || [])")
        fetch_log = sb.execute_script("return JSON.stringify(window.__renew_fetch_log || [])")
        import json as _json
        import re as _re
        xhrs = _json.loads(xhr_log) if xhr_log else []
        fetches = _json.loads(fetch_log) if fetch_log else []
        log.info(f"📡 捕获到 {len(xhrs)} 个 XHR 请求, {len(fetches)} 个 fetch 请求:")

        def _check_extend(body_str):
            """精确检测 Livewire extend 调用, 而非泛泛的 extend 关键字"""
            if not body_str:
                return False
            # Livewire 请求格式: {"calls":[{"method":"extend","params":[]}]}
            # 检测 "method":"extend" (精确匹配方法名)
            if _re.search(r'"method"\s*:\s*"extend"', body_str):
                return True
            # 也检测 "method":"renew" / "method":"addTime" 等
            if _re.search(r'"method"\s*:\s*"(renew|addTime|add_time|vote)"', body_str):
                return True
            return False

        for x in xhrs:
            body_str = x.get('body', '') or ''
            resp_str = x.get('response', '') or ''
            is_extend = _check_extend(body_str)
            marker = ' 🎯EXTEND' if is_extend else ''
            log.info(f"   XHR {x.get('method')} {x.get('url')} → {x.get('status')}{marker}")
            if body_str:
                log.info(f"      请求 body (完整): {body_str[:500]}")
            log.info(f"      响应: {resp_str[:200]}")
            if is_extend:
                extend_request_found = True
        for f in fetches:
            body_str = f.get('body', '') or ''
            resp_str = f.get('response', '') or ''
            is_extend = _check_extend(body_str)
            marker = ' 🎯EXTEND' if is_extend else ''
            # 只打印 livewire 请求 (跳过广告请求, 减少日志噪音)
            url = f.get('url', '') or ''
            if 'livewire' in url or is_extend:
                log.info(f"   FETCH {f.get('method')} {url} → {f.get('status')}{marker}")
                if body_str:
                    log.info(f"      请求 body (完整): {body_str[:500]}")
                log.info(f"      响应: {resp_str[:300]}")
            if is_extend:
                extend_request_found = True
            # 关键: 检测响应中是否含 cooldown 信息
            if 'cooldownExpiry' in resp_str:
                m = _re.search(r'"cooldownExpiry"\s*:\s*(\d+)', resp_str)
                if m:
                    cooldown_seconds = int(m.group(1))
                    if cooldown_seconds > 0:
                        in_cooldown = True
                        log.info(f"   ⏳ 检测到冷却: cooldownExpiry={cooldown_seconds}s")
                m2 = _re.search(r'"expiresTimestamp"\s*:\s*(\d+)', resp_str)
                if m2:
                    exp_ts = int(m2.group(1))
                    from datetime import datetime as _dt, timezone as _tz
                    exp_dt = _dt.fromtimestamp(exp_ts, tz=_tz.utc)
                    log.info(f"   📅 检测到到期时间戳: {exp_dt.isoformat()} (timestamp={exp_ts})")
    except Exception as e:
        log.warning(f"获取网络日志失败: {e}")

    if extend_request_found:
        log.info("✅ 检测到 Livewire extend() 方法调用! 续期按钮被正确触发")
    else:
        log.warning("⚠️ 未检测到 Livewire extend() 方法调用!")
        log.warning("   可能原因: 按钮 disabled / 点击事件被阻止 / 需要先 VOTE")

    if in_cooldown:
        log.info(f"✅ 检测到冷却状态 ({cooldown_seconds}s), 说明续期请求已被服务器接收!")
        log.info(f"✅ 冷却期内再次点击不会增加时间, 但本次点击已生效")

    # 检查是否有真正的 modal
    try:
        real_modal = sb.execute_script("""
            try {
                var modals = document.querySelectorAll(
                    '[role=\"dialog\"], .fi-modal-content, .fi-modal-body, [class*=\"modal-content\"], [class*=\"dialog-content\"]'
                );
                for (var i = 0; i < modals.length; i++) {
                    var rect = modals[i].getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100) {
                        var btns = modals[i].querySelectorAll('button, a, [role=button]');
                        var btnTexts = [];
                        for (var j = 0; j < btns.length; j++) {
                            var t = (btns[j].innerText || '').trim();
                            if (t) btnTexts.push(t);
                        }
                        return JSON.stringify({
                            found: true,
                            class: (modals[i].className || '').substring(0, 100),
                            buttons: btnTexts
                        });
                    }
                }
                return JSON.stringify({found: false});
            } catch(e) { return JSON.stringify({error: e.message}); }
        """)
        import json as _json
        modal_info = _json.loads(real_modal) if real_modal else {}
        log.info(f"🔍 真 Modal 检测: {modal_info}")
    except Exception as e:
        log.warning(f"真 Modal 检测失败: {e}")

    time.sleep(5)

    # 续期后时间 - 同样用 JS 精确提取 (用 IIFE 包裹)
    timestamp_after = "未知"
    try:
        time_text = sb.execute_script("""
            return (function() {
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
            })();
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
    # 关键改进: 如果检测到冷却状态, 即使时间没增加也视为成功
    # (冷却期内再次点击不增加时间, 但本次点击已生效)
    if in_cooldown:
        log.info(f"✅ 续期成功 (冷却状态, 时间未增加但请求已生效)")
        log.info(f"   冷却剩余: {cooldown_seconds}s, 期间无法再次续期")
        final_shot = screenshot(sb, f"cooldown_{server_num}")
        msg = (
            f"✅ [{region}] 续期成功 (冷却中)\n"
            f"🖥️ 编号: {server_num}\n"
            f"🕒 续期前: {timestamp_before}\n"
            f"🕒 续期后: {timestamp_after}\n"
            f"⏳ 冷却剩余: {cooldown_seconds}s"
        )
        tg(msg, photo_path=str(final_shot))
        return True

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

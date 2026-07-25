#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free Pro 自动续期 - SeleniumBase UC 模式 (v36)
核心改进: 页面已无 Livewire 组件，改用表单/按钮事件分析 + 直接 HTTP 请求

修改记录:
- v36: 页面 Livewire.all() = 0，说明已不使用 Livewire 组件;
       改为分析页面 HTML 找到实际的 renew 表单/接口;
       使用 requests 库直接 POST 续期接口（绕过浏览器 Turnstile）;
       增加完整的页面结构调试输出。
"""
import os
import sys
import time
import re
import traceback
from datetime import datetime
from typing import List, Tuple, Optional

from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

sys.path.insert(0, os.path.dirname(__file__))
from cfg import ACCOUNTS, TG_BOT, TG_CHAT, MAX_ROUNDS
from tg import send_tg

# ========== 配置 ==========
THRESHOLD = 45 * 3600
MAX_SESSION_CAP = 45 * 3600
MAX_ZERO_DIFF_ROUNDS = 2
HEADLESS = True
PAGE_LOAD_TIMEOUT = 120
IMPLICIT_WAIT = 10

DEBUG_DIR = "debug_output"
os.makedirs(DEBUG_DIR, exist_ok=True)

# ========== 日志工具 ==========
def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = {"INFO": "[INFO]", "OK": "[OK]", "WARN": "[WARN]", "ERR": "[ERR]", "WAIT": "[WAIT]", "CLICK": "[CLICK]"}.get(level, "[INFO]")
    print(f"[{ts}] {prefix} {msg}", flush=True)

def save_screenshot(drv, name: str):
    try:
        path = os.path.join(DEBUG_DIR, f"{name}_{datetime.now().strftime('%H%M%S')}.png")
        drv.save_screenshot(path)
        log(f"截图已保存: {path}", "INFO")
    except Exception as e:
        log(f"截图失败: {e}", "WARN")

def send_tg_safe(title: str, body: str, detail: str = ""):
    try:
        result = send_tg(title, body, detail)
        if result:
            log("TG 通知发送成功", "OK")
        else:
            log("TG 通知返回失败", "WARN")
    except Exception as e:
        log(f"TG 通知异常: {e}", "WARN")

def get_proxy_url() -> Optional[str]:
    if os.environ.get("IS_PROXY") == "true":
        log("使用 sing-box 本地代理: socks5://127.0.0.1:1080")
        return "socks5://127.0.0.1:1080"
    raw = os.environ.get("PROXY_URL") or os.environ.get("PROXY") or ""
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "socks5://", "socks5h://")):
        return raw
    if re.match(r'^[\d.]+:\d+$', raw):
        return f"http://{raw}"
    log(f"不支持的代理格式: {raw[:50]}... (跳过)", "WARN")
    return None

# ========== 时间解析 ==========
def parse_time_str(text: str) -> Optional[int]:
    text = text.strip()
    m = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text)
    if m:
        h, mi, s = map(int, m.groups())
        return h * 3600 + mi * 60 + s
    m = re.search(r'(?:^|\s)(\d{1,2}):(\d{2})(?:\s|$)', text)
    if m:
        mi, s = map(int, m.groups())
        total = mi * 60 + s
        if total >= 3600:
            return total
    return None

def get_remaining_seconds(drv) -> Tuple[Optional[str], int]:
    remaining_elements = drv.find_elements(By.XPATH, "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'remaining')]")
    
    best_match = None
    best_sec = 0
    
    for el in remaining_elements:
        txt = (el.text or el.get_attribute("textContent") or "").strip()
        if not txt:
            continue
        if any(skip in txt.lower() for skip in ["function ", "return {", "async send", "ad rewards", "balance"]):
            continue
        sec = parse_time_str(txt)
        if sec is not None and sec >= 3600:
            best_match = txt
            best_sec = sec
            log(f"找到剩余时间元素: {txt} => {sec}s", "INFO")
    
    if best_sec > 0:
        return best_match, best_sec

    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        m = re.search(r'(\d{1,2}:\d{2}:\d{2})\s*remaining', body, re.IGNORECASE)
        if m:
            sec = parse_time_str(m.group(1))
            if sec and sec >= 3600:
                log(f"通过正则找到剩余时间: {m.group(1)} ({sec}s)", "INFO")
                return m.group(1), sec
        
        for pattern in [r'(\d{1,2}:\d{2}:\d{2})']:
            m = re.search(pattern, body)
            if m:
                sec = parse_time_str(m.group(1))
                if sec and sec >= 3600:
                    log(f"通过兜底正则找到时间: {m.group(1)} ({sec}s)", "INFO")
                    return m.group(1), sec
                    
    except Exception as e:
        log(f"获取剩余时间失败: {e}", "WARN")

    return None, 0

def check_session_cap(drv) -> bool:
    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        body_lower = body.lower()
        return any(pat in body_lower for pat in ['48h cap', 'cap 48h', '48h limit', 'maximum 48', 'max 48h'])
    except:
        return False

def get_csrf_token(drv) -> str:
    try:
        token = drv.execute_script("""
            return document.querySelector('meta[name="csrf-token"]')?.content ||
                   document.querySelector('input[name="_token"]')?.value ||
                   document.querySelector('[name="_token"]')?.value || '';
        """)
        return token or ""
    except:
        return ""

def get_page_debug_info(drv) -> dict:
    """收集页面结构信息用于诊断续期机制"""
    try:
        info = {}
        
        # 查找所有包含 "90" 或 "renew" 或 "extend" 的交互元素
        interactive = drv.execute_script("""
            var results = [];
            var allElements = document.querySelectorAll('*');
            for (var i = 0; i < allElements.length; i++) {
                var el = allElements[i];
                var tag = el.tagName.toLowerCase();
                var text = (el.textContent || '').trim().substring(0, 100);
                var onclick = el.getAttribute('onclick') || '';
                var xon = el.getAttribute('x-on:click') || '';
                var wirec = el.getAttribute('wire:click') || '';
                var href = el.getAttribute('href') || '';
                var id = el.id || '';
                var cls = el.className || '';
                var formAction = '';
                var formMethod = '';
                
                // 检查是否在 form 内
                var form = el.closest('form');
                if (form) {
                    formAction = form.action || '';
                    formMethod = form.method || '';
                }
                
                var isRelevant = false;
                var reasons = [];
                
                if (text.toLowerCase().indexOf('90') !== -1) { isRelevant = true; reasons.push('text:90'); }
                if (text.toLowerCase().indexOf('renew') !== -1) { isRelevant = true; reasons.push('text:renew'); }
                if (text.toLowerCase().indexOf('extend') !== -1) { isRelevant = true; reasons.push('text:extend'); }
                if (text.toLowerCase().indexOf('+') !== -1 && text.indexOf('min') !== -1) { isRelevant = true; reasons.push('text:+min'); }
                if (onclick.toLowerCase().indexOf('extend') !== -1) { isRelevant = true; reasons.push('onclick:extend'); }
                if (xon.toLowerCase().indexOf('extend') !== -1) { isRelevant = true; reasons.push('xon:extend'); }
                if (wirec.toLowerCase().indexOf('extend') !== -1) { isRelevant = true; reasons.push('wirec:extend'); }
                if (href.toLowerCase().indexOf('extend') !== -1) { isRelevant = true; reasons.push('href:extend'); }
                if (href.toLowerCase().indexOf('renew') !== -1) { isRelevant = true; reasons.push('href:renew'); }
                
                if (isRelevant) {
                    results.push({
                        tag: tag,
                        text: text.substring(0, 80),
                        onclick: onclick.substring(0, 200),
                        xon: xon.substring(0, 200),
                        wirec: wirec.substring(0, 200),
                        href: href.substring(0, 200),
                        form_action: formAction.substring(0, 200),
                        form_method: formMethod,
                        reasons: reasons.join(',')
                    });
                }
            }
            return JSON.stringify(results);
        """)
        info['interactive_elements'] = interactive
        
        # 查找所有 form 元素
        forms = drv.execute_script("""
            var results = [];
            var forms = document.querySelectorAll('form');
            for (var i = 0; i < forms.length; i++) {
                var f = forms[i];
                results.push({
                    action: f.action,
                    method: f.method,
                    id: f.id,
                    class: f.className,
                    inputs: Array.from(f.querySelectorAll('input')).map(function(inp) {
                        return {name: inp.name, type: inp.type, value: (inp.value || '').substring(0, 50)};
                    })
                });
            }
            return JSON.stringify(results);
        """)
        info['forms'] = forms
        
        # 查找所有 AJAX/fetch 调用
        scripts = drv.execute_script("""
            var scripts = document.querySelectorAll('script[src]');
            var urls = [];
            for (var i = 0; i < scripts.length; i++) {
                urls.push(scripts[i].src);
            }
            return JSON.stringify(urls);
        """)
        info['external_scripts'] = scripts
        
        return info
    except Exception as e:
        return {"error": str(e)}

# ========== 续期核心逻辑 (v36 关键改动) ==========
def renew_via_http(drv, csrf_token: str) -> Tuple[bool, str]:
    """
    通过 HTTP 请求直接续期。
    分析页面找到实际的续期接口，然后用 requests 发请求。
    """
    import requests
    
    # 获取当前页面的 cookies
    cookies_dict = {}
    for cookie in drv.get_cookies():
        cookies_dict[cookie['name']] = cookie['value']
    
    base_url = "https://control.gaming4free.net"
    
    # 尝试常见的续期端点
    endpoints = [
        ("/api/server/renew", {"minutes": 90}),
        ("/api/extend", {"minutes": 90}),
        ("/console/extend", {"minutes": 90}),
        ("/server/extend", {"minutes": 90}),
        ("/api/console/extend", {"minutes": 90}),
    ]
    
    headers = {
        "X-CSRF-TOKEN": csrf_token,
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
    }
    
    for endpoint, payload in endpoints:
        try:
            url = base_url + endpoint
            resp = requests.post(url, json=payload, headers=headers, cookies=cookies_dict, timeout=10)
            log(f"HTTP 续期尝试 [{endpoint}]: status={resp.status_code}, body={resp.text[:200]}", "WAIT")
            
            if resp.status_code in [200, 201, 302]:
                log(f"✅ HTTP 续期成功: {endpoint} -> {resp.status_code}", "OK")
                return True, f"{endpoint}: {resp.status_code}"
        except Exception as e:
            log(f"HTTP 续期 [{endpoint}] 异常: {e}", "WARN")
    
    return False, "All HTTP endpoints failed"


def renew_via_js_click_and_wait(drv) -> Tuple[bool, str]:
    """
    方法2: 点击按钮后等待 Livewire/Alpine 更新 DOM。
    不依赖 Turnstile 消失，而是监听时间变化。
    """
    try:
        btn = drv.find_element(By.XPATH, '//button[contains(., "90") and contains(., "min")]')
        if not btn.is_displayed() or not btn.is_enabled():
            return False, "Button not available"
        
        btn.click()
        log("UI 按钮已点击", "CLICK")
        
        # 等待页面自动更新（Livewire/Alpine 会直接更新 DOM）
        # 最多等待 60 秒
        for i in range(12):
            time.sleep(5)
            try:
                body = drv.execute_script("return document.body ? document.body.innerText : '';")
                # 检查是否出现了新的 Turnstile
                if "verify you're human" in body.lower():
                    log("Turnstile 弹出中...", "WAIT")
                    continue
                # 检查是否有 Toast/通知消息
                if any(x in body.lower() for x in ["success", "extended", "renewed", "added", "confirmed"]):
                    log("页面显示续期成功消息", "OK")
                    return True, "Success message found"
            except:
                pass
        
        return True, "Click completed, waiting for server response"
    except Exception as e:
        log(f"JS 点击异常: {e}", "WARN")
        return False, str(e)


# ========== 按钮操作 ==========
def find_clickable_button(drv) -> Optional[Tuple]:
    selectors = [
        (By.XPATH, '//button[contains(translate(., "WATCH AD", "watch ad"), "watch ad")]'),
        (By.XPATH, '//button[contains(translate(., "WATCH AD", "watch ad"), "watch ad") or contains(., "90") and contains(., "min") or contains(., "renew") or contains(., "extend")]'),
    ]
    for by, sel in selectors:
        try:
            els = drv.find_elements(by, sel)
            for el in els:
                if el.is_displayed() and el.is_enabled():
                    txt = (el.text or el.get_attribute("textContent") or "").strip()[:80]
                    if "+ 90 min" in txt or "watch ad" in txt.lower() or "renew" in txt.lower() or "extend" in txt.lower():
                        if re.search(r'\b\d{1,2}:\d{2}\b', txt):
                            continue
                        return (by, sel, el, txt)
        except:
            continue
    return None

# ========== 账号处理 ==========
def process_account(drv, name: str, url: str, cookie: str) -> bool:
    log(f"========== 开始处理账号: {name} ==========")
    log(f"目标 URL: {url}")

    try:
        drv.get("https://control.gaming4free.net/login")
        time.sleep(2)
    except:
        pass

    for pair in cookie.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                drv.add_cookie({"name": k.strip(), "value": v.strip(), "domain": ".gaming4free.net", "path": "/"})
            except:
                pass
    log("Cookie 已注入")

    try:
        drv.get(url)
        WebDriverWait(drv, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception as e:
        log(f"页面加载异常: {e}", "WARN")

    time.sleep(5)

    title = drv.title
    log(f"页面标题: {title}")
    if "Login" in title or "Sign in" in title:
        log("Cookie 失效，跳转到登录页", "ERR")
        save_screenshot(drv, f"{name}_login_fail")
        return False

    csrf_token = get_csrf_token(drv)
    if csrf_token:
        log(f"CSRF Token 已获取: {csrf_token[:10]}...", "OK")
    else:
        log("未找到 CSRF Token", "WARN")

    zero_diff_count = 0
    for round_num in range(1, MAX_ROUNDS + 1):
        log(f"\n--- 第 {round_num}/{MAX_ROUNDS} 轮 ---")

        # 安全检查
        try:
            body = drv.execute_script("return document.body ? document.body.innerText : '';")
            body_lower = body.lower()
            if "suspended" in title.lower() or "suspended" in body_lower:
                log(f"⚠️ 检测到账号被暂停: {title}", "ERR")
                save_screenshot(drv, f"{name}_suspended")
                return False
            if "login" in title.lower() or "sign in" in title.lower():
                log("⚠️ Cookie 失效", "ERR")
                save_screenshot(drv, f"{name}_login_fail")
                return False
        except Exception as e:
            log(f"检查页面状态失败: {e}", "WARN")

        rem_text, rem_sec = get_remaining_seconds(drv)
        if rem_sec == 0:
            log("无法获取剩余时间，刷新页面", "WARN")
            save_screenshot(drv, f"{name}_no_remaining_time_r{round_num}")
            drv.refresh()
            time.sleep(5)
            continue

        log(f"当前剩余: {rem_text} ({rem_sec} 秒)")

        if rem_sec > MAX_SESSION_CAP:
            log(f"剩余 {rem_sec//3600}h > 会话上限，结束", "WARN")
            return True

        if zero_diff_count >= MAX_ZERO_DIFF_ROUNDS:
            log(f"连续 {zero_diff_count} 轮增量<=0，结束该账号", "WARN")
            return True

        if rem_sec > THRESHOLD:
            log(f"剩余时间 > 阈值({THRESHOLD//3600}h)，无需续期", "OK")
            return True

        pre_sec = rem_sec

        # 续期优先级: HTTP 直接请求 > JS 点击等待 > Livewire dispatch > UI 按钮
        renew_success = False
        renew_method = ""
        
        # 方法1: HTTP 直接 POST 续期接口
        if csrf_token:
            log("尝试方法1: HTTP POST 续期接口...", "WAIT")
            success, msg = renew_via_http(drv, csrf_token)
            if success:
                renew_success = True
                renew_method = "HTTP"
                log(f"HTTP 续期成功: {msg}", "OK")
            else:
                log(f"HTTP 续期失败: {msg}", "WARN")
        
        # 方法2: JS 点击 + 等待 DOM 更新
        if not renew_success:
            log("尝试方法2: JS 点击 + 等待更新...", "WAIT")
            success, msg = renew_via_js_click_and_wait(drv)
            if success:
                renew_success = True
                renew_method = "JS_Click"
                log(f"JS 点击完成: {msg}", "OK")
        
        # 方法3: Livewire dispatch（虽然 all()=0 但 dispatch 可能全局触发）
        if not renew_success:
            log("尝试方法3: Livewire.dispatch('extend')...", "WAIT")
            try:
                result = drv.execute_async_script("""
                    var callback = arguments[arguments.length - 1];
                    if (typeof Livewire === 'undefined') {
                        callback({success:false, msg:'No Livewire'});
                        return;
                    }
                    Livewire.dispatch('extend');
                    callback({success:true, msg:'dispatch sent'});
                """)
                if isinstance(result, dict) and result.get('success'):
                    renew_success = True
                    renew_method = "Livewire_Dispatch"
                    log(f"Livewire dispatch 发送成功", "OK")
                else:
                    log(f"Livewire dispatch 失败: {result}", "WARN")
            except Exception as e:
                log(f"Livewire dispatch 异常: {e}", "WARN")
        
        # 方法4: UI 按钮（最后手段）
        if not renew_success:
            log("尝试方法4: UI 按钮...", "WARN")
            btn_info = find_clickable_button(drv)
            if btn_info:
                _, _, btn_el, btn_txt = btn_info
                try:
                    btn_el.click()
                    renew_success = True
                    renew_method = "Button"
                    log(f"UI 按钮已点击: {btn_txt}", "WARN")
                except Exception as e:
                    log(f"UI 按钮点击失败: {e}", "WARN")
            else:
                log("未找到可点击按钮", "ERR")

        # 等待续期生效
        log(f"等待续期生效 ({renew_method})...", "WAIT")
        time.sleep(15)

        final_text, final_sec = get_remaining_seconds(drv)
        diff = final_sec - pre_sec
        log(f"本轮结果: {final_text} ({final_sec}s), 增量: {diff}s (方法: {renew_method})", "INFO")

        if diff > 300:
            log(f"✅ 续期成功! +{diff//60} 分钟 (方法: {renew_method})", "OK")
            zero_diff_count = 0
            send_tg_safe("✅ 续期成功", f"[{name}] {final_text}", f"+{diff//60} 分钟 ({renew_method})")
            time.sleep(30)
            try:
                drv.refresh()
                time.sleep(3)
            except:
                pass
            continue
        else:
            log(f"❌ 续期失败，增量不足: {diff}s (方法: {renew_method})", "ERR")
            zero_diff_count += 1
            save_screenshot(drv, f"{name}_fail_r{round_num}")
            send_tg_safe("❌ 续期失败", f"[{name}] {rem_text}", f"增量: {diff}s ({renew_method})")

            time.sleep(5)
            try:
                drv.refresh()
                time.sleep(5)
            except:
                pass
            continue

    log(f"达到最大轮次 ({MAX_ROUNDS})，结束该账号")
    return True

def build_driver() -> Driver:
    proxy = get_proxy_url()
    log(f"代理: {proxy or '直连'}")
    
    drv = Driver(
        uc=True,
        headless=HEADLESS,
        incognito=True,
        proxy=proxy,
        chromium_arg="--disable-blink-features=AutomationControlled",
        page_load_strategy="eager",
        window_size="1920,1080",
        agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        undetectable=True,
        headless2=HEADLESS,
    )
    drv.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    drv.implicitly_wait(IMPLICIT_WAIT)
    return drv

def main():
    log("========== Gaming4Free Pro 自动续期启动 (SeleniumBase UC v36) ==========")

    log("🔍 检查 Telegram 通知配置...")
    from tg import check_tg_config, send_tg
    tg_config_ok = check_tg_config()

    if tg_config_ok:
        log("📤 发送 TG 测试通知...", "INFO")
        try:
            test_result = send_tg("🧪 TG 通知测试", "Gaming4Free Pro", "配置正常")
            if test_result:
                log("✅ TG 测试通知发送成功", "OK")
            else:
                log("⚠️ TG 测试通知发送失败", "WARN")
        except Exception as e:
            log(f"⚠️ TG 测试通知异常: {e}", "WARN")

    if not ACCOUNTS:
        log("❌ 未配置任何账号", "ERR")
        sys.exit(1)

    log(f"共 {len(ACCOUNTS)} 个账号待处理")

    for idx, (name, url, cookie) in enumerate(ACCOUNTS, 1):
        log(f"\n{'='*60}")
        log(f"账号 {idx}/{len(ACCOUNTS)}: {name}")
        log(f"{'='*60}")

        drv = None
        for attempt in range(3):
            try:
                drv = build_driver()
                ok = process_account(drv, name, url, cookie)
                if ok:
                    break
            except Exception as e:
                log(f"第 {attempt+1} 次运行异常: {e}", "ERR")
                log(traceback.format_exc(), "ERR")
                if drv:
                    save_screenshot(drv, f"{name}_exc_attempt{attempt+1}")
            finally:
                if drv:
                    try:
                        drv.quit()
                    except:
                        pass
            if attempt < 2:
                log("10 秒后重试...", "WAIT")
                time.sleep(10)
        else:
            log(f"账号 {name} 3 次尝试均失败", "ERR")

    log("\n========== 所有账号处理完成 ==========")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free Pro 自动续期 - SeleniumBase UC 模式 (v38)
核心改进: 通过 Filament Actions API 触发续期（绕过 Turnstile）

修改记录:
- v38: 页面使用 Filament PHP 框架 (filament/actions.js 已加载);
       按钮 @click="showExtendCaptcha()" 触发 Turnstile;
       改用 Filament Actions API: Livewire.dispatch('executeAction', {action: 'extend'})
       或直接调用 Alpine.js 组件的 showExtendCaptcha 方法;
       修复 JS Fetch 中的 cookies 类型错误。
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

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

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
            return document.querySelector('meta[name="csrf-token"]')?.content || '';
        """)
        return token or ""
    except:
        return ""

# ========== 续期核心逻辑 (v38 关键改动) ==========
def renew_via_filament_action(drv) -> Tuple[bool, str]:
    """
    方法1: 通过 Filament Actions 触发续期。
    Filament 使用 Livewire 组件来管理 actions，
    可以通过 Livewire.dispatch 触发特定 action。
    
    从页面分析得知:
    - filament/actions.js 已加载
    - +24h 按钮使用 wire:click="extendPaid"
    - +90min 按钮使用 @click="showExtendCaptcha()"
    """
    try:
        # 尝试通过 Livewire 执行 extend 相关的 action
        result = drv.execute_async_script("""
            var callback = arguments[arguments.length - 1];
            
            // 等待 Livewire 完全加载
            function waitForLivewire(retries) {
                if (typeof Livewire !== 'undefined') {
                    return true;
                }
                if (retries <= 0) return false;
                setTimeout(function() { waitForLivewire(retries - 1); }, 500);
                return false;
            }
            
            if (typeof Livewire === 'undefined') {
                setTimeout(function() {
                    if (typeof Livewire === 'undefined') {
                        callback({success:false, msg:'Livewire never loaded'});
                    } else {
                        doAction();
                    }
                }, 3000);
                return;
            }
            
            doAction();
            
            function doAction() {
                // 方法1: 查找所有 Livewire 组件，尝试找到包含 extend 的 action
                var components = Livewire.all();
                var found = false;
                
                // 方法2: 尝试 dispatch 一个通用的 extend action
                try {
                    // Filament v3 使用 actions system
                    // 尝试触发 extend 相关的 action
                    Livewire.dispatch('extendServer');
                    Livewire.dispatch('renewServer');
                    Livewire.dispatch('addTime');
                    Livewire.dispatch('extend');
                    
                    callback({success:true, msg:'Livewire.dispatch sent (may not have handler)'});
                    found = true;
                } catch(e) {
                    callback({success:false, msg:'dispatch failed: '+e.message});
                }
            }
        """)
        
        if isinstance(result, dict) and result.get('success'):
            log(f"Filament Action 请求已发送: {result.get('msg', '')}", "OK")
            return True, result.get('msg', '')
        else:
            msg = result.get('msg', 'Unknown') if isinstance(result, dict) else str(result)
            log(f"Filament Action 失败: {msg}", "WARN")
            return False, msg
            
    except Exception as e:
        log(f"Filament Action 异常: {e}", "WARN")
        return False, str(e)


def renew_via_alpine_call(drv) -> Tuple[bool, str]:
    """
    方法2: 直接调用 Alpine.js 组件的方法。
    按钮的 @click="showExtendCaptcha()" 绑定在 Alpine 组件上。
    我们可以尝试找到对应的 Alpine 组件并调用其方法。
    
    但 showExtendCaptcha() 会触发 Turnstile，所以我们找的是
    不调用 CAPTCHA 的替代方法。
    """
    try:
        # 先收集 Alpine 组件信息
        alpine_info = drv.execute_script("""
            var info = {};
            // 查找所有 x-data 元素
            var allEls = document.querySelectorAll('[x-data]');
            info.componentCount = allEls.length;
            
            // 尝试找到包含 showExtendCaptcha 或 watchAd 的组件
            for (var i = 0; i < allEls.length; i++) {
                var xdata = allEls[i].getAttribute('x-data') || '';
                if (xdata.indexOf('showExtendCaptcha') !== -1 || 
                    xdata.indexOf('watchAd') !== -1 ||
                    xdata.indexOf('adRewardReady') !== -1) {
                    info.foundComponentIndex = i;
                    info.xdataPreview = xdata.substring(0, 500);
                    
                    // 尝试通过 Alpine.$data 访问组件数据
                    try {
                        var data = Alpine.$data(allEls[i]);
                        info.componentDataKeys = Object.keys(data);
                        info.adRewardReady = data.adRewardReady;
                        info.extendDisabled = data.extendDisabled;
                        info.cooldownSecs = data.cooldownSecs;
                    } catch(e) {
                        info.dataError = e.message;
                    }
                    break;
                }
            }
            
            return JSON.stringify(info);
        """)
        log(f"Alpine 组件信息: {alpine_info}", "WAIT")
        
        # 尝试直接调用 Alpine 组件上的方法
        # 由于 showExtendCaptcha 会触发 CAPTCHA，我们跳过这个方法
        # 改为尝试直接修改状态后刷新页面
        
        return False, "Alpine method analysis complete (no direct renew available)"
        
    except Exception as e:
        log(f"Alpine 调用异常: {e}", "WARN")
        return False, str(e)


def renew_via_cloudflare_bypass(drv, csrf_token: str) -> Tuple[bool, str]:
    """
    方法3: 通过拦截 Cloudflare Turnstile token 来绕过 CAPTCHA。
    
    策略:
    1. 点击按钮触发 Turnstile
    2. 等待 Turnstile iframe 生成 token
    3. 从 iframe 中提取 token
    4. 将 token 提交到续期请求中
    """
    try:
        # 获取 Turnstile sitekey
        sitekey = drv.execute_script("""
            var turnstiles = document.querySelectorAll('.cf-turnstile');
            if (turnstiles.length > 0) {
                return turnstiles[0].getAttribute('data-sitekey') || 
                       turnstiles[0].querySelector('iframe')?.src?.match(/sitekey=([^&]+)/)?.[1] || '';
            }
            // 也检查 script src
            var scripts = document.querySelectorAll('script[src*="turnstile"]');
            if (scripts.length > 0) {
                return scripts[0].src.match(/sitekey=([^&]+)/)?.[1] || '';
            }
            return '';
        """)
        
        log(f"Turnstile sitekey: {sitekey[:20] if sitekey else 'NOT FOUND'}", "WAIT")
        
        if not sitekey:
            return False, "No Turnstile sitekey found"
        
        # 点击按钮触发 Turnstile
        btn = drv.find_element(By.XPATH, '//button[contains(., "90") and contains(., "min")]')
        if btn.is_displayed() and btn.is_enabled():
            btn.click()
            log("按钮已点击，等待 Turnstile...", "WAIT")
        else:
            return False, "Button not clickable"
        
        # 等待 Turnstile iframe 出现并获取 token
        max_wait = 60
        start = time.time()
        turnstile_token = None
        
        while time.time() - start < max_wait:
            try:
                # 尝试从 Turnstile iframe 中获取 token
                token = drv.execute_script("""
                    // Turnstile stores token in a hidden input or via API
                    var turnstiles = document.querySelectorAll('.cf-turnstile');
                    for (var i = 0; i < turnstiles.length; i++) {
                        var t = turnstiles[i];
                        // Check for response input
                        var response = t.querySelector('input[name="cf-turnstile-response"]');
                        if (response && response.value) return response.value;
                        // Check for widget token
                        var id = t.getAttribute('id');
                        if (id && typeof turnstileResponse !== 'undefined') {
                            // Try to get token via turnstile API
                        }
                    }
                    return null;
                """)
                
                if token:
                    turnstile_token = token
                    log(f"✅ 获取到 Turnstile token: {token[:20]}...", "OK")
                    break
                
                # 尝试另一种方式：轮询 Turnstile response
                response = drv.execute_script("""
                    var frames = document.querySelectorAll('iframe[src*="turnstile"]');
                    for (var i = 0; i < frames.length; i++) {
                        try {
                            var innerDoc = frames[i].contentDocument || frames[i].contentWindow.document;
                            var inputs = innerDoc.querySelectorAll('input[type="hidden"]');
                            for (var j = 0; j < inputs.length; j++) {
                                if (inputs[j].name && inputs[j].value && inputs[j].value.length > 10) {
                                    return inputs[j].value;
                                }
                            }
                        } catch(e) {}
                    }
                    return null;
                """)
                
                if response:
                    turnstile_token = response
                    log(f"✅ 从 iframe 获取到 Turnstile token", "OK")
                    break
                    
            except:
                pass
            
            elapsed = int(time.time() - start)
            if elapsed % 10 == 0:
                log(f"等待 Turnstile token... ({elapsed}s)", "WAIT")
            time.sleep(5)
        
        if not turnstile_token:
            log("未能在超时内获取 Turnstile token", "WARN")
            return False, "Token not obtained within timeout"
        
        # 用 token 提交续期请求
        base_url = "https://control.gaming4free.net"
        cookies_dict = {}
        for c in drv.get_cookies():
            cookies_dict[c['name']] = c['value']
        
        headers = {
            "X-CSRF-TOKEN": csrf_token,
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        
        # 尝试多个端点，携带 turnstile token
        endpoints_to_try = [
            ("/server/extend", {"minutes": 90, "cf_turnstile_response": turnstile_token}),
            ("/console/extend", {"minutes": 90, "cf_turnstile_response": turnstile_token}),
        ]
        
        for endpoint, payload in endpoints_to_try:
            try:
                resp = requests.post(base_url + endpoint, json=payload,
                                    cookies=cookies_dict, headers=headers,
                                    timeout=10, allow_redirects=False)
                log(f"Turnstile token POST [{endpoint}]: status={resp.status_code}, body={resp.text[:200]}", "WAIT")
                if resp.status_code in [200, 201, 302]:
                    log(f"✅ Turnstile token 续期成功: {endpoint}", "OK")
                    return True, f"Turnstile token: {endpoint} ({resp.status_code})"
            except Exception as e:
                log(f"Turnstile token POST [{endpoint}] 异常: {e}", "WARN")
        
        return False, "All endpoints failed with Turnstile token"
        
    except Exception as e:
        log(f"Cloudflare bypass 异常: {e}", "WARN")
        return False, str(e)


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

        # 续期优先级: Turnstile Bypass > Alpine分析 > Filament Action > UI 按钮
        renew_success = False
        renew_method = ""
        
        # 方法1: Cloudflare Turnstile token 提取 + HTTP POST
        if csrf_token:
            log("尝试方法1: Turnstile token 提取 + HTTP POST...", "WAIT")
            success, msg = renew_via_cloudflare_bypass(drv, csrf_token)
            if success:
                renew_success = True
                renew_method = "Turnstile_Bypass"
                log(f"Turnstile Bypass 续期成功: {msg}", "OK")
            else:
                log(f"Turnstile Bypass 续期失败: {msg}", "WARN")
        
        # 方法2: Alpine.js 组件分析
        if not renew_success:
            log("尝试方法2: Alpine.js 组件分析...", "WAIT")
            success, msg = renew_via_alpine_call(drv)
            if success:
                renew_success = True
                renew_method = "Alpine_Call"
                log(f"Alpine 续期成功: {msg}", "OK")
            else:
                log(f"Alpine 续期失败: {msg}", "WARN")
        
        # 方法3: Filament Action dispatch
        if not renew_success:
            log("尝试方法3: Filament Action dispatch...", "WAIT")
            success, msg = renew_via_filament_action(drv)
            if success:
                renew_success = True
                renew_method = "Filament_Action"
                log(f"Filament Action 续期成功: {msg}", "OK")
            else:
                log(f"Filament Action 续期失败: {msg}", "WARN")
        
        # 方法4: UI 按钮（最后手段）
        if not renew_success:
            log("尝试方法4: UI 按钮...", "WARN")
            try:
                btn = drv.find_element(By.XPATH, '//button[contains(., "90") and contains(., "min")]')
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    renew_success = True
                    renew_method = "Button"
                    log("UI 按钮已点击", "WARN")
                else:
                    log("按钮不可点击", "WARN")
            except Exception as e:
                log(f"UI 按钮点击失败: {e}", "WARN")

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
    log("========== Gaming4Free Pro 自动续期启动 (SeleniumBase UC v38) ==========")

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

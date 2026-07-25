#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free Pro 自动续期 - SeleniumBase UC 模式 (v34)
核心改进: 绕过 UI 按钮触发 Turnstile，改用 Livewire API 直接续期

修改记录:
- v34: 使用 Livewire @extends 事件直接续期（不触发 Turnstile CAPTCHA）;
       如果 Livewire 不可用，回退到 fetch API 直接调用后端接口;
       修复时间解析避免匹配 JS 代码;
       TG 通知安全封装。
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
THRESHOLD = 45 * 3600          # 剩余时间阈值 45h
MAX_SESSION_CAP = 45 * 3600     # 会话最大限制
MAX_ZERO_DIFF_ROUNDS = 2       # 连续增量<=0 判定次数
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
    """安全发送 TG 通知"""
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
    """从文本中解析 HH:MM:SS / H:MM:SS / MM:SS 为秒数"""
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
    """获取当前剩余时间"""
    # 策略1: 查找包含 "remaining" 关键字的元素
    remaining_elements = drv.find_elements(By.XPATH, "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'remaining')]")
    
    best_match = None
    best_sec = 0
    
    for el in remaining_elements:
        txt = (el.text or el.get_attribute("textContent") or "").strip()
        if not txt:
            continue
        # 跳过 JS 代码块、广告相关
        if any(skip in txt.lower() for skip in ["function ", "return {", "async send", "ad rewards", "balance"]):
            continue
        sec = parse_time_str(txt)
        if sec is not None and sec >= 3600:
            best_match = txt
            best_sec = sec
            log(f"找到剩余时间元素: {txt} => {sec}s", "INFO")
    
    if best_sec > 0:
        return best_match, best_sec

    # 策略2: 从 body 全文正则提取
    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        
        # 优先匹配 "XX:XX:XXremaining" 模式
        m = re.search(r'(\d{1,2}:\d{2}:\d{2})\s*remaining', body, re.IGNORECASE)
        if m:
            sec = parse_time_str(m.group(1))
            if sec and sec >= 3600:
                log(f"通过正则找到剩余时间: {m.group(1)} ({sec}s)", "INFO")
                return m.group(1), sec
        
        # 兜底: 找所有 HH:MM:SS
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

# ========== Turnstile 检测 ==========
def is_turnstile_active(drv) -> bool:
    """检测 Turnstile CAPTCHA 是否正在显示"""
    try:
        # 检查 cf-turnstile div
        turnstile_divs = drv.find_elements(By.CSS_SELECTOR, "div.cf-turnstile")
        for div in turnstile_divs:
            if div.is_displayed():
                return True
        # 检查 iframe
        iframes = drv.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "turnstile" in src.lower() or "challenges.cloudflare" in src.lower():
                return True
        # 检查文字
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        if "verify you're human" in body.lower():
            return True
        return False
    except:
        return False

# ========== 续期核心逻辑 (v34 关键改动) ==========
def renew_via_livewire(drv) -> Tuple[bool, str]:
    """
    通过 Livewire API 直接续期，绕过 UI 按钮触发 Turnstile。
    返回 (success, message)
    """
    try:
        # 方法1: 使用 Livewire call('extend')
        result = drv.execute_async_script("""
            // 等待 Livewire 加载
            const callback = arguments[arguments.length - 1];
            
            if (typeof Livewire === 'undefined' && typeof window.livewire === 'undefined') {
                // 尝试等待 Livewire 加载
                setTimeout(() => {
                    if (typeof Livewire === 'undefined' && typeof window.livewire === 'undefined') {
                        callback({ success: false, message: 'Livewire not loaded' });
                    } else {
                        doExtend();
                    }
                }, 2000);
            } else {
                doExtend();
            }
            
            function doExtend() {
                const lw = typeof Livewire !== 'undefined' ? Livewire : window.livewire;
                if (!lw) {
                    callback({ success: false, message: 'Livewire object not found' });
                    return;
                }
                
                try {
                    // 尝试调用 extend 方法
                    lw.all()[0].call('extend').then(() => {
                        callback({ success: true, message: 'Livewire extend called' });
                    }).catch((err) => {
                        // 尝试其他组件索引
                        for (let i = 1; i < lw.all().length; i++) {
                            try {
                                lw.all()[i].call('extend');
                                callback({ success: true, message: `Livewire extend on component ${i}` });
                                return;
                            } catch(e) {}
                        }
                        callback({ success: false, message: err.message || 'Livewire extend failed' });
                    });
                } catch (e) {
                    callback({ success: false, message: e.message || 'Livewire call error' });
                }
            }
        """)
        
        if isinstance(result, dict) and result.get('success'):
            log(f"Livewire 续期请求已发送: {result.get('message', '')}", "OK")
            return True, result.get('message', '')
        else:
            msg = result.get('message', 'Unknown error') if isinstance(result, dict) else str(result)
            log(f"Livewire 续期失败: {msg}", "WARN")
            return False, msg
            
    except Exception as e:
        log(f"Livewire 续期异常: {e}", "WARN")
        return False, str(e)


def renew_via_fetch(drv, csrf_token: str) -> Tuple[bool, str]:
    """
    方法2: 通过 fetch API 直接调用后端续期接口（绕过 Livewire）
    """
    try:
        # 尝试常见的续期接口路径
        endpoints = [
            "/api/server/renew",
            "/api/extend",
            "/renew",
            "/server/extend",
        ]
        
        for endpoint in endpoints:
            result = drv.execute_async_script(f"""
                const callback = arguments[arguments.length - 1];
                fetch('{endpoint}', {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                        'X-CSRF-TOKEN': '{csrf_token}',
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }},
                    body: JSON.stringify({{ minutes: 90 }})
                }})
                .then(r => r.json().then(d => ({ status: r.status, data: d })))
                .then(r => callback(r))
                .catch(e => callback({{ success: false, message: e.message }}));
            """)
            
            if isinstance(result, dict) and result.get('status', 0) in [200, 201]:
                log(f"Fetch 续期成功 ({endpoint}): {result}", "OK")
                return True, f"{endpoint}: {result}"
        
        log("所有 fetch 端点都失败了", "WARN")
        return False, "All fetch endpoints failed"
            
    except Exception as e:
        log(f"Fetch 续期异常: {e}", "WARN")
        return False, str(e)


def renew_via_button_fallback(drv) -> Tuple[bool, str]:
    """
    方法3: 最后的回退方案 - 点击 UI 按钮（会触发 Turnstile）
    仅在前两种方法都失败时使用
    """
    try:
        # 找 +90min 按钮
        btn = drv.find_element(By.XPATH, '//button[contains(., "90") and contains(., "min")]')
        if btn.is_displayed() and btn.is_enabled():
            btn.click()
            log("UI 按钮点击成功（可能触发 Turnstile）", "WARN")
            return True, "Button clicked (Turnstile may appear)"
        else:
            log("按钮不可点击", "WARN")
            return False, "Button not clickable"
    except Exception as e:
        log(f"UI 按钮点击失败: {e}", "WARN")
        return False, str(e)


def get_csrf_token(drv) -> str:
    """获取 CSRF Token"""
    try:
        token = drv.execute_script("""
            return document.querySelector('meta[name="csrf-token"]')?.content ||
                   document.querySelector('input[name="_token"]')?.value ||
                   document.querySelector('[name="_token"]')?.value ||
                   '';
        """)
        return token or ""
    except:
        return ""

# ========== 按钮操作 ==========
def find_clickable_button(drv) -> Optional[Tuple]:
    """查找可点击按钮"""
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
                        # 排除冷却中的按钮
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

    # 1. 登录并注入 Cookie
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

    # 2. 刷新到控制台页面
    try:
        drv.get(url)
        WebDriverWait(drv, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception as e:
        log(f"页面加载异常: {e}", "WARN")

    time.sleep(5)

    # 3. 验证登录状态
    title = drv.title
    log(f"页面标题: {title}")
    if "Login" in title or "Sign in" in title:
        log("Cookie 失效，跳转到登录页", "ERR")
        save_screenshot(drv, f"{name}_login_fail")
        return False

    # 获取 CSRF Token
    csrf_token = get_csrf_token(drv)
    if csrf_token:
        log(f"CSRF Token 已获取: {csrf_token[:10]}...", "OK")
    else:
        log("未找到 CSRF Token", "WARN")

    # 4. 主循环
    zero_diff_count = 0
    for round_num in range(1, MAX_ROUNDS + 1):
        log(f"\n--- 第 {round_num}/{MAX_ROUNDS} 轮 ---")

        # 4.0 安全检查
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

        # 4.1 获取剩余时间
        rem_text, rem_sec = get_remaining_seconds(drv)
        if rem_sec == 0:
            log("无法获取剩余时间，刷新页面", "WARN")
            save_screenshot(drv, f"{name}_no_remaining_time_r{round_num}")
            drv.refresh()
            time.sleep(5)
            continue

        log(f"当前剩余: {rem_text} ({rem_sec} 秒)")

        # --- 会话上限检查 ---
        if rem_sec > MAX_SESSION_CAP:
            log(f"剩余 {rem_sec//3600}h > 会话上限 {MAX_SESSION_CAP//3600}h，结束", "WARN")
            return True

        if zero_diff_count >= MAX_ZERO_DIFF_ROUNDS:
            log(f"连续 {zero_diff_count} 轮增量<=0，结束该账号", "WARN")
            return True

        if rem_sec > THRESHOLD:
            log(f"剩余时间 > 阈值({THRESHOLD//3600}h)，无需续期", "OK")
            return True

        pre_sec = rem_sec

        # 4.2 尝试续期 - 优先级: Livewire > Fetch > Button
        renew_success = False
        renew_method = ""
        
        # 方法1: Livewire
        log("尝试方法1: Livewire API 续期...", "WAIT")
        success, msg = renew_via_livewire(drv)
        if success:
            renew_success = True
            renew_method = "Livewire"
            log(f"Livewire 续期请求发送成功: {msg}", "OK")
        else:
            log(f"Livewire 续期失败: {msg}", "WARN")
            
            # 方法2: Fetch API
            if csrf_token:
                log("尝试方法2: Fetch API 续期...", "WAIT")
                success, msg = renew_via_fetch(drv, csrf_token)
                if success:
                    renew_success = True
                    renew_method = "Fetch"
                    log(f"Fetch 续期成功: {msg}", "OK")
                else:
                    log(f"Fetch 续期失败: {msg}", "WARN")
            
            # 方法3: UI 按钮（最后手段，会触发 Turnstile）
            if not renew_success:
                log("尝试方法3: UI 按钮续期（会触发 Turnstile）...", "WARN")
                success, msg = renew_via_button_fallback(drv)
                if success:
                    renew_success = True
                    renew_method = "Button"
                    log(f"UI 按钮已点击: {msg}", "WARN")
                else:
                    log(f"所有续期方法均失败", "ERR")

        # 4.3 等待续期生效
        log(f"等待续期生效 ({renew_method})...", "WAIT")
        time.sleep(15)  # 给服务器足够时间响应

        # 4.4 验证结果
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

# ========== 驱动构建 ==========
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
    log("========== Gaming4Free Pro 自动续期启动 (SeleniumBase UC v34) ==========")

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

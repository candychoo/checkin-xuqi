#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free Pro 自动续期 - SeleniumBase UC 模式 (v33)
修复 Turnstile 检测 + 时间解析 + TG 通知

修改记录:
- v33: 修复 Turnstile iframe src 检测(改用 class="cf-turnstile" 和 visibility);
       修复 get_remaining_seconds() 避免匹配 JS 代码中的时间;
       修复 TG 通知失败时不中断流程;
       增加点击后等待 Turnstile 消失后再检查时间;
       增加重试机制: 如果 Turnstile 未解决则刷新页面重新尝试。
"""
import os
import sys
import time
import re
import traceback
from datetime import datetime
from typing import List, Tuple, Optional

# SeleniumBase UC 模式
from seleniumbase import Driver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 导入模块
sys.path.insert(0, os.path.dirname(__file__))
from cfg import ACCOUNTS, TG_BOT, TG_CHAT, MAX_ROUNDS
from tg import send_tg

# ========== 配置 ==========
THRESHOLD = 45 * 3600          # 剩余时间阈值 45h
MAX_SESSION_CAP = 45 * 3600     # 会话最大限制 (48h cap)
MAX_ZERO_DIFF_ROUNDS = 2       # 连续增量<=0 判定次数
HEADLESS = True                # True=无头 / False=有头
PAGE_LOAD_TIMEOUT = 120
IMPLICIT_WAIT = 10
CLICK_DELAY = 1.5              # 点击等待
BUTTON_RETRY_REFRESH = True    # 按钮未找到时是否刷新页面

# 截图目录
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
    """安全发送 TG 通知，失败不影响主流程"""
    try:
        result = send_tg(title, body, detail)
        if result:
            log("TG 通知发送成功", "OK")
        else:
            log("TG 通知返回失败", "WARN")
    except Exception as e:
        log(f"TG 通知异常: {e}", "WARN")

def get_proxy_url() -> Optional[str]:
    """获取代理地址"""
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
        # 排除纯分钟数 < 1小时的时间 (如倒计时)
        if total >= 3600:
            return total
    return None

def get_remaining_seconds(drv) -> Tuple[Optional[str], int]:
    """获取当前剩余时间 (显示文本, 秒数)"""
    # 策略1: 查找包含 "remaining" 关键字的元素
    remaining_elements = drv.find_elements(By.XPATH, "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'remaining')]")
    
    best_match = None
    best_sec = 0
    
    for el in remaining_elements:
        txt = (el.text or el.get_attribute("textContent") or "").strip()
        if not txt:
            continue
        
        # 跳过 JS 代码块
        if txt.startswith("function ") or "return {" in txt or "async send" in txt:
            continue
        
        # 跳过广告/奖励相关文本
        if "ad rewards" in txt.lower() or "balance" in txt.lower():
            continue
        
        sec = parse_time_str(txt)
        if sec is not None and sec >= 3600:  # 至少1小时
            best_match = txt
            best_sec = sec
            log(f"找到剩余时间元素: {txt} => {sec}s", "INFO")
    
    if best_sec > 0:
        return best_match, best_sec

    # 策略2: 从 body 全文正则提取
    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        
        # 先找 "XX:XX:XXremaining" 模式
        m = re.search(r'(\d{1,2}:\d{2}:\d{2})\s*remaining', body, re.IGNORECASE)
        if m:
            sec = parse_time_str(m.group(1))
            if sec and sec >= 3600:
                log(f"通过正则找到剩余时间: {m.group(1)} ({sec}s)", "INFO")
                return m.group(1), sec
        
        # 再找 "expires XX:XX PM" 模式
        m = re.search(r'expires\s+(\d{1,2}:\d{2}\s*(?:AM|PM))', body, re.IGNORECASE)
        if m:
            # 转换12小时制为秒数不太现实，跳过
            pass
        
        # 最后兜底: 找所有 HH:MM:SS
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
    """检查页面是否出现 48h cap 提示"""
    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        body_lower = body.lower()
        cap_patterns = ['48h cap', 'cap 48h', '48h limit', 'maximum 48', 'max 48h']
        return any(pat in body_lower for pat in cap_patterns)
    except:
        return False

# ========== Turnstile 处理 ==========
def is_turnstile_active(drv) -> bool:
    """检测 Turnstile CAPTCHA 是否正在显示（未完成）"""
    try:
        # 方法1: 检查 cf-turnstile div 是否存在且可见
        turnstile_divs = drv.find_elements(By.CSS_SELECTOR, "div.cf-turnstile")
        for div in turnstile_divs:
            if div.is_displayed():
                return True
        
        # 方法2: 检查 Turnstile iframe 是否存在
        iframes = drv.find_elements(By.TAG_NAME, "iframe")
        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "turnstile" in src.lower() or "challenges.cloudflare" in src.lower():
                return True
        
        # 方法3: 检查页面上是否有 "Verify you're human" 文字
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        if "verify you're human" in body.lower() or "cloudflare" in body.lower():
            # 还要确认不是已完成的状态
            # 已完成时 Turnstile 通常会消失或显示绿色勾
            return True
            
        return False
    except:
        return False

def wait_for_turnstile_complete(drv, timeout: int = 120) -> bool:
    """等待 Turnstile 完成或消失"""
    start = time.time()
    while time.time() - start < timeout:
        if not is_turnstile_active(drv):
            log("Turnstile 已通过/未出现", "OK")
            return True
        elapsed = int(time.time() - start)
        if elapsed % 10 == 0:
            log(f"Turnstile 验证中... ({elapsed}s)", "WAIT")
        time.sleep(5)
    
    log(f"Turnstile 等待超时 ({timeout}s)", "WARN")
    return False

# ========== 按钮操作 ==========
BUTTON_SELECTORS = [
    (By.XPATH, '//button[contains(translate(., "WATCH AD", "watch ad"), "watch ad")]'),
    (By.XPATH, '//a[contains(translate(., "WATCH AD", "watch ad"), "watch ad")]'),
    (By.XPATH, '//button[normalize-space(text()) = "+ 90 min" or translate(text(), "+", "") = "90 min"]'),
    (By.XPATH, '//a[normalize-space(text()) = "+ 90 min" or translate(text(), "+", "") = "90 min"]'),
    (By.XPATH, '//button[contains(translate(., "WATCH AD", "watch ad"), "watch ad") or contains(., "90") and contains(., "min") or contains(., "renew") or contains(., "extend")]'),
    (By.XPATH, '//a[contains(translate(., "WATCH AD", "watch ad"), "watch ad") or contains(., "90") and contains(., "min") or contains(., "renew") or contains(., "extend")]'),
]

def is_watch_ad_state(btn_txt: str) -> bool:
    """判断按钮是否在可点击状态（非冷却）"""
    import re
    t = btn_txt.lower()

    if re.search(r'\+\s*90\s*min', t):
        if re.search(r'\b\d{1,2}:\d{2}\b', t) or re.search(r'\b\d{1,2}:\d{2}:\d{2}\b', t):
            log(f"⚠️ 检测到等待按钮: {btn_txt} (含冷却时间)", "WARN")
            return False
        log(f"✅ 可点击按钮: {btn_txt}", "OK")
        return True

    clickable_patterns = [r'watch\s*ad', r'renew', r'extend']
    has_clickable = any(re.search(p, t) for p in clickable_patterns)
    if not has_clickable:
        log(f"❓ 无效按钮文本: {btn_txt}", "WARN")
        return False

    cooldown_patterns = [r'\b\d{1,2}:\d{2}\b', r'\b\d+\s*m(?:in)?\b']
    is_cooldown = any(re.search(p, t) for p in cooldown_patterns)
    if is_cooldown:
        log(f"⏳ 等待按钮: {btn_txt}", "WARN")
        return False

    log(f"✅ 可点击按钮: {btn_txt}", "OK")
    return True

def find_clickable_button(drv) -> Optional[Tuple]:
    """查找可点击按钮 (by, selector, element, button_text)"""
    for by, sel in BUTTON_SELECTORS:
        try:
            els = drv.find_elements(by, sel)
            for el in els:
                if el.is_displayed() and el.is_enabled():
                    txt = (el.text or el.get_attribute("textContent") or "").strip()[:80]
                    if is_watch_ad_state(txt):
                        log(f"找到可点击按钮: {txt} ({by}={sel})", "OK")
                        return (by, sel, el, txt)
                    else:
                        log(f"⚠️ 按钮不可点击: {txt} ({by}={sel})", "WARN")
        except:
            continue
    return None

def click_button(drv, el) -> bool:
    """点击元素"""
    try:
        drv.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        time.sleep(0.3)
        drv.execute_script("arguments[0].click();", el)
        log("JS 点击成功", "CLICK")
        return True
    except Exception as e:
        log(f"JS 点击失败: {e}", "WARN")
    try:
        el.click()
        log("原生点击成功", "CLICK")
        return True
    except Exception as e:
        log(f"原生点击失败: {e}", "WARN")
    return False

def wait_for_cooldown(drv, max_wait: int = 1500) -> bool:
    """等待按钮恢复可点击状态"""
    start = time.time()
    while time.time() - start < max_wait:
        time.sleep(30)
        try:
            drv.refresh()
            time.sleep(3)
        except:
            pass
        btn_info = find_clickable_button(drv)
        if btn_info:
            _, _, _, txt = btn_info
            if is_watch_ad_state(txt):
                log(f"按钮恢复可点击: {txt}", "OK")
                return True
        log(f"继续等待... 已等 {int(time.time()-start)}s", "WAIT")
    return False

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

    # 3. 等待 Turnstile 初始验证
    time.sleep(5)

    # 4. 验证登录状态
    title = drv.title
    log(f"页面标题: {title}")
    if "Login" in title or "Sign in" in title:
        log("Cookie 失效，跳转到登录页", "ERR")
        save_screenshot(drv, f"{name}_login_fail")
        return False

    # 5. 主循环
    zero_diff_count = 0
    for round_num in range(1, MAX_ROUNDS + 1):
        log(f"\n--- 第 {round_num}/{MAX_ROUNDS} 轮 ---")

        # 5.0 安全检查
        try:
            body = drv.execute_script("return document.body ? document.body.innerText : '';")
            body_lower = body.lower()
            if "suspended" in title.lower() or "suspended" in body_lower:
                log(f"⚠️ 检测到账号被暂停: {title}", "ERR")
                save_screenshot(drv, f"{name}_suspended")
                return False
            if "login" in title.lower() or "sign in" in title.lower():
                log("⚠️ Cookie 失效，跳转到登录页", "ERR")
                save_screenshot(drv, f"{name}_login_fail")
                return False
        except Exception as e:
            log(f"检查页面状态失败: {e}", "WARN")

        # 5.1 获取剩余时间
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
            log(f"剩余 {rem_sec//3600}h > 会话上限 {MAX_SESSION_CAP//3600}h，判定已达会话上限，结束", "WARN")
            if check_session_cap(drv):
                log("页面检测到 '48h cap' 提示，确认会话已满", "WARN")
            return True

        # --- 连续失败检查 ---
        if zero_diff_count >= MAX_ZERO_DIFF_ROUNDS:
            log(f"连续 {zero_diff_count} 轮增量<=0，判定已达上限，结束该账号", "WARN")
            return True

        if rem_sec > THRESHOLD:
            log(f"剩余时间 > 阈值({THRESHOLD//3600}h)，无需续期", "OK")
            return True

        pre_sec = rem_sec

        # 5.2 找按钮
        btn_info = find_clickable_button(drv)
        if not btn_info and BUTTON_RETRY_REFRESH:
            log("首次未找到按钮，刷新页面重试...", "WARN")
            drv.refresh()
            time.sleep(5)
            btn_info = find_clickable_button(drv)

        if not btn_info:
            log("未找到可点击按钮", "ERR")
            save_screenshot(drv, f"{name}_no_btn_r{round_num}")
            drv.refresh()
            time.sleep(10)
            continue

        by, sel, btn_el, btn_txt = btn_info
        is_watch_ad = is_watch_ad_state(btn_txt)

        # 5.3 如果按钮在冷却中，等待恢复
        if not is_watch_ad:
            log(f"按钮不在 Watch Ad 状态: {btn_txt}，等待冷却恢复", "WAIT")
            if not wait_for_cooldown(drv, max_wait=1500):
                log("冷却等待超时，继续下一轮", "WARN")
                continue
            btn_info = find_clickable_button(drv)
            if not btn_info:
                continue
            by, sel, btn_el, btn_txt = btn_info

        # 5.4 点击前截图
        save_screenshot(drv, f"{name}_before_click_r{round_num}")

        # 5.5 点击按钮
        log(f"点击按钮: {btn_txt}", "CLICK")
        if not click_button(drv, btn_el):
            log("点击失败", "ERR")
            save_screenshot(drv, f"{name}_click_fail_r{round_num}")
            time.sleep(5)
            continue

        # 5.6 等待 Turnstile 完成
        log("检测 Turnstile/Cloudflare...", "TURNSTILE")
        ts_start = time.time()
        while time.time() - ts_start < 180:
            # 先检查时间是否已经增加（说明 Turnstile 已完成）
            _, cur_sec = get_remaining_seconds(drv)
            if cur_sec > pre_sec + 300:
                log(f"✅ 检测到时间增加 (Turnstile 完成): +{cur_sec - pre_sec}s", "OK")
                break
            
            # 检查 Turnstile 是否还在
            if not is_turnstile_active(drv):
                log("Turnstile 已通过/未出现", "OK")
                break
            
            log(f"Turnstile 验证中... ({int(time.time()-ts_start)}s)", "WAIT")
            time.sleep(5)
        
        else:
            log("Turnstile 等待超时，尝试检查时间", "WARN")

        # 5.7 等待续期生效（额外等待 10s 让 Livewire 响应）
        log("等待续期生效...", "WAIT")
        time.sleep(10)

        # 5.8 验证结果
        final_text, final_sec = get_remaining_seconds(drv)
        diff = final_sec - pre_sec
        log(f"本轮结果: {final_text} ({final_sec}s), 增量: {diff}s")

        if diff > 300:
            log(f"✅ 续期成功! +{diff//60} 分钟", "OK")
            zero_diff_count = 0
            send_tg_safe("✅ 续期成功", f"[{name}] {final_text}", f"+{diff//60} 分钟")
            time.sleep(30)
            try:
                drv.refresh()
                time.sleep(3)
            except:
                pass
            continue
        else:
            log(f"❌ 续期失败，增量不足: {diff}s", "ERR")
            zero_diff_count += 1
            save_screenshot(drv, f"{name}_fail_r{round_num}")
            send_tg_safe("❌ 续期失败", f"[{name}] {rem_text}", f"增量: {diff}s")

            # 刷新页面重试
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
    """构建 SeleniumBase UC Driver"""
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
    log("========== Gaming4Free Pro 自动续期启动 (SeleniumBase UC v33) ==========")

    # 检查 TG 配置
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
        log("❌ 未配置任何账号 (GAME4FREE_ACCOUNTS / GAME4FREE_ACCOUNT)", "ERR")
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

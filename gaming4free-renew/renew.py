#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free Pro 服务器自动续期 - SeleniumBase UC 模式
兼容 Turnstile/Cloudflare 验证，支持代理，多账号轮询

修复记录:
- v32: 增加会话上限检测 (48h cap)，剩余>6h 跳过续期；连续失败自动停止；按钮未找到时刷新重试
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

# 本地模块
sys.path.insert(0, os.path.dirname(__file__))
from cfg import ACCOUNTS, TG_BOT, TG_CHAT, MAX_ROUNDS
from tg import send_tg

# ========== 配置 ==========
THRESHOLD = 45 * 3600          # 剩余时间低于 45h 才续期
MAX_SESSION_CAP = 45 * 3600     # 会话上限保护：剩余 > 45h 视为已达上限，跳过续期 (实测 48h cap 但广告仅在快过期时生效)
MAX_ZERO_DIFF_ROUNDS = 2       # 连续多少轮增量<=0 判定达上限，结束该账号
HEADLESS = True                # True=无头，False=有头（调试用）
PAGE_LOAD_TIMEOUT = 120
IMPLICIT_WAIT = 10
CLICK_DELAY = 1.5              # 点击后等待
BUTTON_RETRY_REFRESH = True    # 按钮未找到时是否刷新重试一次

# 调试截图目录
DEBUG_DIR = "debug_output"
os.makedirs(DEBUG_DIR, exist_ok=True)

# ========== 工具函数 ==========
def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = {"INFO": "📋", "OK": "✅", "WARN": "⚠️", "ERR": "❌", "WAIT": "⏳", "CLICK": "🖱️"}.get(level, "•")
    print(f"[{ts}] {prefix} {msg}", flush=True)

def save_screenshot(drv, name: str):
    try:
        path = os.path.join(DEBUG_DIR, f"{name}_{datetime.now().strftime('%H%M%S')}.png")
        drv.save_screenshot(path)
        log(f"截图已保存: {path}", "INFO")
    except Exception as e:
        log(f"截图失败: {e}", "WARN")

def get_proxy_url() -> Optional[str]:
    """获取代理地址：
    1. 优先用工作流 sing-box 建立的本地 socks5://127.0.0.1:1080 (IS_PROXY=true)
    2. 回退解析 PROXY_URL 基础格式
    """
    # 1. 工作流 sing-box 成功时会设置 IS_PROXY=true
    if os.environ.get("IS_PROXY") == "true":
        log("使用 sing-box 本地代理: socks5://127.0.0.1:1080")
        return "socks5://127.0.0.1:1080"

    # 2. 解析 PROXY_URL 环境变量
    raw = os.environ.get("PROXY_URL") or os.environ.get("PROXY") or ""
    raw = raw.strip()
    if not raw:
        return None

    # 已经是标准格式
    if raw.startswith(("http://", "https://", "socks5://", "socks5h://")):
        return raw

    # 简单 ip:port
    if re.match(r'^[\d.]+:\d+$', raw):
        return f"http://{raw}"

    # VLESS/VMess/TUIC 等复杂链接：无法直接用，记录警告并返回 None（直连）
    log(f"代理链接格式不支持直接使用: {raw[:50]}... (将直连)", "WARN")
    return None

# ========== 核心：解析剩余时间 ==========
def parse_remaining_time(text: str) -> Optional[int]:
    """从文本提取剩余秒数，支持 HH:MM:SS / H:MM:SS / MM:SS"""
    text = text.strip()
    # HH:MM:SS
    m = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text)
    if m:
        h, m_, s = map(int, m.groups())
        return h * 3600 + m_ * 60 + s
    # MM:SS
    m = re.search(r'(?:^|\s)(\d{1,2}):(\d{2})(?:\s|$)', text)
    if m:
        m_, s = map(int, m.groups())
        return m_ * 60 + s
    return None

def get_remaining_seconds(drv) -> Tuple[Optional[str], int]:
    """获取当前剩余时间 (显示文本, 总秒数)"""
    # 1. 优先找包含 remaining 的元素
    for xpath in [
        "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'remaining')]",
        "//*[contains(@class, 'remaining')]",
        "//*[contains(@id, 'remaining')]",
    ]:
        try:
            els = drv.find_elements(By.XPATH, xpath)
            for el in els:
                txt = (el.text or el.get_attribute("textContent") or "").strip()
                sec = parse_remaining_time(txt)
                if sec is not None:
                    return txt, sec
        except:
            pass

    # 2. 全文正则兜底
    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        for pattern in [
            r'(\d{1,2}:\d{2}:\d{2})\s*remaining',
            r'remaining[^\d]*(\d{1,2}:\d{2}:\d{2})',
            r'(\d{1,2}:\d{2}:\d{2})',
        ]:
            m = re.search(pattern, body, re.IGNORECASE)
            if m:
                sec = parse_remaining_time(m.group(1))
                if sec is not None:
                    return m.group(1), sec
    except:
        pass

    return None, 0

def check_session_cap(drv) -> bool:
    """检测页面是否包含 48h cap / 会话上限提示"""
    try:
        body = drv.execute_script("return document.body ? document.body.innerText : '';")
        body_lower = body.lower()
        # 常见上限关键词
        cap_patterns = ['48h cap', 'cap 48h', '48h limit', 'maximum 48', 'max 48h', 'session cap']
        for pat in cap_patterns:
            if pat in body_lower:
                return True
    except:
        pass
    return False

def is_watch_ad_state(btn_txt: str) -> bool:
    """判断按钮是否处于可点击的续期状态（Watch Ad / + 90 min 等）"""
    import re
    t = btn_txt.lower()
    
    # 特殊处理："+ 90 min" 是可点击状态（无倒计时）
    # 但 "+ 90 min 05:00" 是冷却状态（有倒计时）
    if re.search(r'\+\s*90\s*min', t):
        # 检查是否有倒计时
        if re.search(r'\b\d{1,2}:\d{2}\b', t) or re.search(r'\b\d{1,2}:\d{2}:\d{2}\b', t):
            return False  # 冷却状态
        return True  # 可点击状态
    
    # 可点击的续期按钮特征
    clickable_patterns = [
        r'watch\s*ad',           # Watch Ad
        r'watch',                # Watch
        r'renew',               # Renew
        r'extend',              # Extend
    ]
    
    has_clickable = any(re.search(p, t) for p in clickable_patterns)
    if not has_clickable:
        return False
    
    # 冷却特征：含倒计时时间格式 (MM:SS / H:MM:SS / 5m / 5min / 5 min)
    cooldown_patterns = [
        r'\b\d{1,2}:\d{2}\b',           # 05:00, 5:00, 12:34 (MM:SS 或 H:MM)
        r'\b\d{1,2}:\d{2}:\d{2}\b',     # 1:05:00 (H:MM:SS)
        r'\b\d+\s*m(?:in)?\b',          # 5m, 5min, 5 min
    ]
    
    is_cooldown = any(re.search(p, t) for p in cooldown_patterns)
    return not is_cooldown


# ========== 核心：按钮检测与点击 ==========
BUTTON_SELECTORS = [
    # 优先：文本匹配
    (By.XPATH, '//button[contains(translate(., "WATCH AD", "watch ad"), "watch ad")]'),
    (By.XPATH, '//a[contains(translate(., "WATCH AD", "watch ad"), "watch ad")]'),
    (By.XPATH, '//button[contains(., "90") and contains(., "min")]'),
    (By.XPATH, '//a[contains(., "90") and contains(., "min")]'),
    (By.XPATH, '//*[@role="button"][contains(., "90") and contains(., "min")]'),
    # 兜底：class/id 常见模式
    (By.CSS_SELECTOR, 'button.btn-renew, button.renew-btn, button[onclick*="renew"], a[href*="renew"]'),
    (By.CSS_SELECTOR, '.renew-button, #renew-button, .btn-renew'),
]

CONFIRM_SELECTORS = [
    (By.XPATH, '//button[contains(., "Confirm")]'),
    (By.XPATH, '//button[contains(., "Yes")]'),
    (By.XPATH, '//button[contains(., "OK")]'),
    (By.XPATH, '//button[contains(., "Renew")]'),
    (By.XPATH, '//button[contains(., "Extend")]'),
    (By.CSS_SELECTOR, '.swal2-confirm, .modal-footer button.btn-primary, .btn-confirm'),
]

def find_clickable_button(drv) -> Optional[Tuple[str, str]]:
    """返回 (by, selector, element, button_text) 或 None"""
    for by, sel in BUTTON_SELECTORS:
        try:
            els = drv.find_elements(by, sel)
            for el in els:
                if el.is_displayed() and el.is_enabled():
                    txt = (el.text or el.get_attribute("textContent") or "").strip()[:80]
                    log(f"找到按钮: {txt} ({by}={sel})", "OK")
                    return (by, sel, el, txt)
        except:
            continue
    return None

def click_button(drv, el) -> bool:
    """多策略点击"""
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

def handle_confirm_dialog(drv) -> bool:
    """处理确认弹窗 / alert"""
    # 1. 网页模态框
    for by, sel in CONFIRM_SELECTORS:
        try:
            btn = WebDriverWait(drv, 3).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log(f"点击确认按钮: {sel}", "OK")
            time.sleep(1)
            return True
        except:
            continue
    # 2. 原生 alert
    try:
        alert = drv.switch_to.alert
        log(f"检测到 Alert: {alert.text}", "WARN")
        alert.accept()
        return True
    except:
        pass
    return False

# ========== 核心：等待冷却恢复 ==========
def wait_for_cooldown(drv, max_wait: int = 1500) -> bool:
    """等待按钮变回 Watch Ad 状态（默认 25 分钟）"""
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
                log(f"冷却结束，按钮可用: {txt}", "OK")
                return True
        log(f"仍在冷却中... 已等 {int(time.time()-start)}s", "WAIT")
    return False

# ========== 单账号续期流程 ==========
def process_account(drv, name: str, url: str, cookie: str) -> bool:
    log(f"========== 开始处理账号: {name} ==========")
    log(f"目标 URL: {url}")

    # 1. 先访问登录页注入 Cookie
    try:
        drv.get("https://control.gaming4free.net/login")
        time.sleep(2)
    except:
        pass

    # 注入 Cookie
    for pair in cookie.split(";"):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                drv.add_cookie({"name": k.strip(), "value": v.strip(), "domain": ".gaming4free.net", "path": "/"})
            except:
                pass
    log("Cookie 已注入")

    # 2. 访问服务器页面
    try:
        drv.get(url)
        # 等待页面加载（可能有 CF/Turnstile） - 使用标准 Selenium 等待
        WebDriverWait(drv, 30).until(lambda d: d.execute_script("return document.readyState") == "complete")
    except Exception as e:
        log(f"页面加载异常: {e}", "WARN")

    # 3. 等待 Turnstile/Cloudflare 通过（UC 模式自动处理，额外等待）
    time.sleep(5)

    # 4. 验证是否登录成功
    title = drv.title
    log(f"页面标题: {title}")
    if "Login" in title or "Sign in" in title:
        log("Cookie 失效，仍在登录页", "ERR")
        save_screenshot(drv, f"{name}_login_fail")
        return False

    # 5. 多轮续期
    zero_diff_count = 0  # 连续增量<=0 的轮次
    for round_num in range(1, MAX_ROUNDS + 1):
        log(f"\n--- 第 {round_num}/{MAX_ROUNDS} 轮 ---")

        # 5.1 获取当前剩余时间
        rem_text, rem_sec = get_remaining_seconds(drv)
        if rem_sec == 0:
            log("无法获取剩余时间，刷新重试", "WARN")
            drv.refresh()
            time.sleep(5)
            continue

        log(f"当前剩余: {rem_text} ({rem_sec} 秒)")

        # --- 新增：会话上限保护 ---
        if rem_sec > MAX_SESSION_CAP:
            log(f"剩余 {rem_sec//3600}h > 保护上限 {MAX_SESSION_CAP//3600}h，判定已达会话上限，跳过续期", "WARN")
            # 再确认一次页面是否有 cap 提示
            if check_session_cap(drv):
                log("页面检测到 '48h cap' 字样，确认会话上限", "WARN")
            return True  # 正常结束，不算失败

        # --- 新增：连续失败保护 ---
        if zero_diff_count >= MAX_ZERO_DIFF_ROUNDS:
            log(f"连续 {zero_diff_count} 轮增量<=0，判定已达上限，结束该账号", "WARN")
            return True

        if rem_sec > THRESHOLD:
            log(f"剩余时间 > 阈值({THRESHOLD//3600}h)，无需续期", "OK")
            return True

        pre_sec = rem_sec

        # 5.2 找按钮（含一次刷新重试）
        btn_info = find_clickable_button(drv)
        if not btn_info and BUTTON_RETRY_REFRESH:
            log("首次未找到按钮，刷新页面重试...", "WARN")
            drv.refresh()
            time.sleep(5)
            btn_info = find_clickable_button(drv)

        if not btn_info:
            log("未找到续期按钮", "ERR")
            save_screenshot(drv, f"{name}_no_btn_r{round_num}")
            drv.refresh()
            time.sleep(10)
            continue

        by, sel, btn_el, btn_txt = btn_info
        is_watch_ad = is_watch_ad_state(btn_txt)

        # 5.3 如果在冷却，等待恢复
        if not is_watch_ad:
            log(f"按钮非 Watch Ad 状态: {btn_txt}，进入冷却等待", "WAIT")
            if not wait_for_cooldown(drv, max_wait=1500):
                log("冷却等待超时，跳过本轮", "WARN")
                continue
            # 冷却后重新获取按钮
            btn_info = find_clickable_button(drv)
            if not btn_info:
                continue
            by, sel, btn_el, btn_txt = btn_info

        # 5.4 点击按钮
        log(f"点击按钮: {btn_txt}", "CLICK")
        if not click_button(drv, btn_el):
            log("点击失败", "ERR")
            save_screenshot(drv, f"{name}_click_fail_r{round_num}")
            time.sleep(5)
            continue

        # 5.5 处理确认弹窗
        time.sleep(CLICK_DELAY)
        handle_confirm_dialog(drv)

        # 5.6 等待续期生效（最多 30s）
        log("等待续期生效...", "WAIT")
        success = False
        wait_end = time.time() + 30
        while time.time() < wait_end:
            time.sleep(3)
            _, cur_sec = get_remaining_seconds(drv)
            if cur_sec > pre_sec + 300:  # 至少增加 5 分钟
                diff = cur_sec - pre_sec
                log(f"检测到时间增加: +{diff} 秒 ({diff//60} 分)", "OK")
                success = True
                break

        # 5.7 最终验证
        final_text, final_sec = get_remaining_seconds(drv)
        diff = final_sec - pre_sec
        log(f"本轮结果: {final_text} ({final_sec}s), 增量: {diff}s")

        if diff > 300:
            log(f"🎉 续期成功! +{diff//60} 分钟", "OK")
            zero_diff_count = 0  # 重置连续失败计数
            try:
                send_tg(f"🎉 [{name}] Pro 续期成功 (+{diff//60}分钟)", name, final_text)
            except:
                pass
            # 成功后等待 30s 再下一轮
            time.sleep(30)
            try:
                drv.refresh()
                time.sleep(3)
            except:
                pass
            continue
        else:
            log(f"续期失败，增量不足: {diff}s", "ERR")
            zero_diff_count += 1
            save_screenshot(drv, f"{name}_fail_r{round_num}")
            time.sleep(10)
            try:
                drv.refresh()
                time.sleep(3)
            except:
                pass
            continue

    log(f"达到最大轮次 ({MAX_ROUNDS})，结束该账号")
    return True

# ========== 主入口 ==========
def build_driver() -> Driver:
    """创建 SeleniumBase UC Driver"""
    proxy = get_proxy_url()
    log(f"代理: {proxy or '直连'}")
    
    drv = Driver(
        uc=True,                    # Undetected-Chromedriver 模式（绕过 CF/Turnstile）
        headless=HEADLESS,
        incognito=True,
        proxy=proxy,
        chromium_arg="--disable-blink-features=AutomationControlled",
        page_load_strategy="eager",
        window_size="1920,1080",
        agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # SeleniumBase 特有参数
        undetectable=True,          # 启用更强的反检测
        headless2=HEADLESS,         # 新版 headless 模式
    )
    drv.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    drv.implicitly_wait(IMPLICIT_WAIT)
    return drv

def main():
    log("========== Gaming4Free Pro 自动续期启动 (SeleniumBase UC v32) ==========")

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
                log(f"第 {attempt+1} 次尝试异常: {e}", "ERR")
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

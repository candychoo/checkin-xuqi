#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gaming4free 自动续期脚本（GHA + sing-box proxy + seleniumbase UC mode）
================================================================
- 使用 seleniumbase UC mode 反检测
- 走 sing-box SOCKS5 代理出口（CF 自家 IP，几乎必过 Turnstile）
- 自动识别续期按钮，循环点击至 48h 上限
- 点击前后剩余时间对比，确保真成功
- 失败自动截图 + Telegram 通知
"""
import os
import re
import sys
import time
import json
import random
import socket
import logging
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# 配置区
# ---------------------------------------------------------------------------
SITE_URL       = os.getenv("GF_SITE_URL", "https://gaming4free.zapto.org/")
LOGIN_URL      = os.getenv("GF_LOGIN_URL", "")
USERNAME       = os.getenv("MC_USERNAME", "")
PASSWORD       = os.getenv("MC_PASSWORD", "")
COOKIE_STR     = os.getenv("GF_COOKIE", "")

# 代理地址：workflow 中 sing-box 默认监听 1080 端口
PROXY_URL      = os.getenv("PROXY_SOCKS5", "socks5://127.0.0.1:1080")

MAX_HOURS      = 48            # 续期上限 48 小时
ADD_MINUTES    = 90            # 每次点击 +90 分钟
COOLDOWN_SEC   = 240           # 冷却 4 分钟
MAX_CLICKS     = 30            # 单次运行最大点击次数（防死循环）
PAGE_TIMEOUT   = 60            # 单页操作超时
TURNSTILE_WAIT = 90            # Turnstile 等待上限

TG_TOKEN       = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID     = os.getenv("TG_CHAT_ID", "")

SHOT_DIR       = Path("screenshots")
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
# 工具函数
# ---------------------------------------------------------------------------
def tg(msg: str):
    """Telegram 通知（失败不影响主流程）"""
    if not (TG_TOKEN and TG_CHAT_ID):
        log.warning("TG 未配置，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
        log.info("✅ TG 通知发送成功")
    except Exception as e:
        log.warning(f"TG 通知失败: {e}")


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
        # 排除太小的值（如秒级倒计时）
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


def human_sleep(a: float = 0.5, b: float = 1.5):
    """模拟人类反应时间"""
    time.sleep(random.uniform(a, b))


def screenshot(sb, name: str):
    """保存截图，返回路径"""
    p = SHOT_DIR / f"{datetime.now():%H%M%S}_{name}.png"
    try:
        sb.save_screenshot(str(p))
        log.info(f"截图: {p}")
    except Exception as e:
        log.warning(f"截图失败: {e}")
    return p


# ---------------------------------------------------------------------------
# 续期核心
# ---------------------------------------------------------------------------
def get_remaining_seconds(sb) -> int:
    """从页面提取剩余时间，返回秒数（-1 表示无法识别）"""
    try:
        selectors = [
            "#timeleft", ".timeleft", ".time-left",
            "#remaining", ".remaining", ".countdown",
            '[class*="time"]', '[id*="time"]',
            '[class*="remain"]', '[id*="remain"]',
        ]
        for sel in selectors:
            try:
                txt = sb.get_text(sel) if sb.is_element_visible(sel) else ""
                sec = parse_remaining_seconds(txt)
                if sec > 0:
                    log.info(f"剩余时间 [{sel}] = {txt} -> {sec}s ({sec//3600}h {(sec%3600)//60}m)")
                    return sec
            except Exception:
                continue

        # 兜底：整页文本提取
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


def livewire_extend(sb) -> dict:
    """使用 Livewire JavaScript 直接调用 extend 方法，返回结果"""
    from util import _LW_EXTEND_V3_JS, _LW_V2_JS, _LW_CLICK_JS
    
    results = []
    
    # 策略1: Livewire v3 - $wire.call('extend')
    try:
        result = sb.execute_script(_LW_EXTEND_V3_JS)
        if result:
            log.info(f"Livewire v3 结果: {result}")
            results.append(result)
    except Exception as e:
        log.warning(f"Livewire v3 调用失败: {e}")
    
    # 策略2: Livewire v2 - livewire.emit('extend')
    try:
        result = sb.execute_script(_LW_V2_JS)
        if result:
            log.info(f"Livewire v2 结果: {result}")
            results.append(result)
    except Exception as e:
        log.warning(f"Livewire v2 调用失败: {e}")
    
    # 策略3: 直接点击 90 min 按钮
    try:
        result = sb.execute_script(_LW_CLICK_JS)
        if result:
            log.info(f"按钮点击结果: {result}")
            results.append(result)
    except Exception as e:
        log.warning(f"按钮点击失败: {e}")
    
    return {"results": results, "success": any("success" in r.lower() or "clicked" in r.lower() or "call_extend" in r.lower() for r in results)}


def click_renew_button(sb) -> bool:
    """找到并点击续期按钮（备用方案）"""
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
    for sel in candidates:
        try:
            if sb.is_element_visible(sel, timeout=5):
                human_sleep(1.0, 2.5)
                sb.scroll_to(sel)
                human_sleep(0.3, 0.8)
                try:
                    sb.click(sel, timeout=8)
                except Exception:
                    sb.execute_script("document.querySelector(arguments[0]).click();", sel)
                log.info(f"点击续期按钮 [{sel}]")
                return True
        except Exception:
            continue
    log.warning("未找到续期按钮")
    return False


def handle_turnstile(sb) -> bool:
    """处理 Cloudflare Turnstile，返回是否通过"""
    try:
        iframe_sel = 'iframe[src*="challenges.cloudflare.com"]'
        if not sb.is_element_present(iframe_sel, timeout=3):
            log.info("未检测到 Turnstile iframe，跳过")
            return True

        log.info("检测到 Cloudflare Turnstile，等待自动通过...")
        screenshot(sb, "turnstile_appear")

        for i in range(TURNSTILE_WAIT):
            try:
                val = sb.execute_script("""
                    let el = document.querySelector('[name="cf-turnstile-response"]');
                    if (!el) el = document.querySelector('input[name*="turnstile"]');
                    return el ? el.value : '';
                """)
                if val and len(val) > 20:
                    log.info(f"Turnstile 已通过 ({i}s)")
                    return True
            except Exception:
                pass

            if i == 3:
                try:
                    sb.switch_to_frame(iframe_sel)
                    try:
                        if sb.is_element_visible('input[type="checkbox"]'):
                            sb.click('input[type="checkbox"]', timeout=3)
                            log.info("点击 Turnstile checkbox")
                    except Exception:
                        pass
                    finally:
                        sb.switch_to_default_content()
                except Exception:
                    pass

            time.sleep(1)

        log.warning(f"Turnstile {TURNSTILE_WAIT}s 未通过")
        screenshot(sb, "turnstile_timeout")
        return False
    except Exception as e:
        log.warning(f"Turnstile 处理异常: {e}")
        return True


def inject_cookies(sb):
    """如果提供了 cookie 字符串，注入到当前域名"""
    if not COOKIE_STR:
        return
    log.info("注入自定义 cookie ...")
    for item in COOKIE_STR.split(";"):
        if "=" in item:
            k, v = item.strip().split("=", 1)
            try:
                sb.set_cookie(k, v)
            except Exception:
                pass


def do_login(sb):
    """登录逻辑（如有）"""
    if not USERNAME:
        log.info("未配置 MC_USERNAME，跳过登录")
        return
    log.info(f"尝试登录用户: {USERNAME}")

    user_selectors = ['input[name="username"]', 'input[name="user"]',
                      'input[name="mc_username"]', 'input[type="text"]',
                      'input[id*="user"]', 'input[name="email"]']
    pass_selectors = ['input[name="password"]', 'input[type="password"]']
    submit_selectors = ['button[type="submit"]', 'input[type="submit"]',
                         'button:contains("Login")', 'button:contains("登录")',
                         'button:contains("Sign in")']

    for sel in user_selectors:
        try:
            if sb.is_element_visible(sel):
                sb.type(sel, USERNAME, timeout=5)
                break
        except Exception:
            continue
    if PASSWORD:
        for sel in pass_selectors:
            try:
                if sb.is_element_visible(sel):
                    sb.type(sel, PASSWORD, timeout=5)
                    break
            except Exception:
                continue
    for sel in submit_selectors:
        try:
            if sb.is_element_visible(sel):
                human_sleep(0.5, 1.2)
                sb.click(sel, timeout=5)
                log.info("登录表单已提交")
                time.sleep(3)
                return
        except Exception:
            continue


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run():
    from seleniumbase import SB

    # 解析代理端口用于预检
    port_match = re.search(r':(\d+)$', PROXY_URL.rstrip('/'))
    proxy_port = int(port_match.group(1)) if port_match else 1080

    log.info("=" * 60)
    log.info("gaming4free 续期启动")
    log.info(f"代理地址: {PROXY_URL}")
    log.info(f"目标站点: {SITE_URL}")
    log.info(f"MC 用户:  {USERNAME or '(未配置)'}")
    log.info("=" * 60)

    # 预检代理端口
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", proxy_port))
        s.close()
        log.info(f"代理 SOCKS5 端口 {proxy_port} 可用")
    except Exception:
        log.error(f"代理端口 {proxy_port} 不可达，请检查代理启动状态")
        tg(f"❌ gaming4free 续期失败：代理端口 {proxy_port} 未就绪")
        sys.exit(1)

    # UC mode 启动
    log.info("正在启动浏览器 (uc=True, headless=True)...")
    with SB(
        browser="chrome",
        uc=False,
        headless=True,
        incognito=False,
        agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        disable_cookies=False,
        proxy=PROXY_URL,
        ad_block=False,
    ) as sb:
        log.info("✅ 浏览器启动成功")

        sb.set_window_size(1280, 800)
        sb.driver.set_page_load_timeout(PAGE_TIMEOUT)

        # Step 1: 打开站点
        try:
            sb.open(SITE_URL)
            sb.sleep(2)
        except Exception as e:
            log.error(f"打开站点失败: {e}")
            screenshot(sb, "open_fail")
            tg(f"❌ gaming4free 续期失败：站点打不开\n{e}")
            return

        # Step 2: 处理 CF 5 秒盾（如有）
        log.info("等待 CF 5 秒盾（如有）...")
        for _ in range(15):
            if "just a moment" in sb.get_text("body").lower():
                time.sleep(1)
            else:
                break

        # Step 3: 注入 cookie / 登录
        inject_cookies(sb)
        if LOGIN_URL:
            sb.open(LOGIN_URL)
            sb.sleep(2)
            do_login(sb)
            sb.open(SITE_URL)
            sb.sleep(2)

        screenshot(sb, "dashboard")

        # Step 4: 主循环 - 反复点击续期直到接近 48h
        click_count = 0
        last_sec = get_remaining_seconds(sb)
        log.info(f"初始剩余: {last_sec}s ({last_sec//3600}h {(last_sec%3600)//60}m)")

        while click_count < MAX_CLICKS:
            # 接近上限就停
            if last_sec >= (MAX_HOURS - 1) * 3600:
                log.info(f"已接近 {MAX_HOURS}h 上限，停止续期")
                break

            # Step 4.1: 先尝试 Livewire 方法，失败再试按钮点击
            log.info("尝试 Livewire extend...")
            lw_result = livewire_extend(sb)
            if lw_result["success"]:
                log.info("✅ Livewire extend 成功")
            else:
                log.warning("Livewire extend 失败，尝试按钮点击...")
                if not click_renew_button(sb):
                    screenshot(sb, f"no_btn_{click_count}")
                    log.warning("本次未找到按钮，可能需要刷新页面")
                    sb.refresh()
                    sb.sleep(3)
                    last_sec = get_remaining_seconds(sb)
                    continue

            # Step 4.2: 处理可能出现的 Turnstile
            human_sleep(1.0, 2.0)
            handle_turnstile(sb)

            # Step 4.3: 等待响应
            human_sleep(3.0, 5.0)
            sb.sleep(2)

            # Step 4.4: 对比时间
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

            # 冷却
            if last_sec >= (MAX_HOURS - 1) * 3600:
                break
            log.info(f"冷却 {COOLDOWN_SEC}s ...")
            for i in range(COOLDOWN_SEC, 0, -10):
                log.info(f"  剩 {i}s")
                time.sleep(10)

        # 收尾
        final_sec = get_remaining_seconds(sb)
        h, m = final_sec // 3600, (final_sec % 3600) // 60
        msg = (f"gaming4free 续期完成\n"
               f"成功点击: {click_count} 次\n"
               f"最终剩余: {h}h {m}m")
        log.info(msg)
        tg(msg)
        screenshot(sb, "final")


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        log.info("用户中断")
    except Exception as e:
        log.exception(f"未捕获异常: {e}")
        tg(f"❌ gaming4free 续期崩溃\n{e}")
        sys.exit(1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gaming4free 自动续期脚本（GHA + sing-box proxy + seleniumbase UC mode）
===============================================================
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
from datetime import datetime, timedelta
from pathlib import Path
from selenium.webdriver.common.action_chains import ActionChains

# ---------------------------------------------------------------------------
# 配置区
# ---------------------------------------------------------------------------
SITE_URL       = os.getenv("GF_SITE_URL", "https://gaming4free.zapto.org/")
LOGIN_URL      = os.getenv("GF_LOGIN_URL", "")
USERNAME       = os.getenv("MC_USERNAME", "")
PASSWORD       = os.getenv("MC_PASSWORD", "")
COOKIE_STR     = os.getenv("GF_COOKIE", "")

# 代理地址：优先用 secrets.PROXY_URL，回退到本地 sing-box
_raw_proxy = os.getenv("PROXY_URL", "").strip()
if "?" in _raw_proxy or (_raw_proxy and not _raw_proxy.startswith(("socks", "http"))):
    PROXY_URL = _raw_proxy
elif _raw_proxy:
    PROXY_URL = _raw_proxy
else:
    PROXY_URL = "socks5://127.0.0.1:1080"

MAX_HOURS      = 48            # 续期上限 48 小时
ADD_MINUTES    = 90            # 每次点击 +90 分钟
COOLDOWN_SEC   = 240           # 冷却 4 分钟
MAX_CLICKS     = 30            # 单次运行最大点击次数
PAGE_TIMEOUT   = 60            # 单页操作超时
TURNSTILE_WAIT = 90            # Turnstile 等待上限

TG_TOKEN       = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID     = os.getenv("TG_CHAT_ID", "")

# 多服务器配置：格式 "1,US|2,CN|3,EU"
SERVERS_ENV    = os.getenv("SERVERS", "").strip()
SERVER_LIST    = []
if SERVERS_ENV:
    for item in SERVERS_ENV.split("|"):
        try:
            num, region = item.split(",", 1)
            SERVER_LIST.append({"num": num.strip(), "region": region.strip()})
        except ValueError:
            pass

SHOT_DIR       = Path("artifacts")
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


def screenshot(sb, name: str):
    """保存截图，返回路径"""
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
        h, m, s = map(int, t_str.strip().split(':'))
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


# ---------------------------------------------------------------------------
# Cloudflare Turnstile 破解
# ---------------------------------------------------------------------------
def bypass_turnstile(sb) -> bool:
    """手动破解 Cloudflare Turnstile，返回是否成功"""
    try:
        # 查找 Turnstile iframe
        cf_iframe = None
        iframes = sb.driver.find_elements("tag name", "iframe")
        for f in iframes:
            src = f.get_attribute("src")
            if src and ("cloudflare" in src.lower() or "turnstile" in src.lower()):
                cf_iframe = f
                break

        if not cf_iframe:
            log.info("未检测到 Turnstile iframe，跳过")
            return True

        size = cf_iframe.size
        width = size.get('width', 0)
        log.info(f"🎯 锁定 Turnstile iframe! 尺寸: {width}x{size.get('height', '?')}")

        if width > 0:
            center_x_offset = int(-(width / 2) + 30)
            # 尝试点击 iframe 内 checkbox
            for offset in [center_x_offset - 15, center_x_offset, center_x_offset + 15]:
                try:
                    ac = ActionChains(sb.driver)
                    ac.move_to_element(cf_iframe).move_by_offset(offset, 0).click().perform()
                    time.sleep(0.5)
                except Exception:
                    pass

        # 等待验证回调
        token = ""
        for attempt in range(4):
            log.info(f"⏳ 等待验证回调 ({attempt+1}/4)...")
            time.sleep(6)
            try:
                token = sb.execute_script(
                    "return document.querySelector('[name=\"cf-turnstile-response\"]') ? "
                    "document.querySelector('[name=\"cf-turnstile-response\"]').value : ''"
                )
            except Exception:
                pass
            if token and len(token) > 20:
                log.info("✅ 成功！已获取 Cloudflare 凭证")
                return True

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

    # 策略1: Livewire v3
    try:
        result = sb.execute_script(_LW_EXTEND_V3_JS)
        if result:
            log.info(f"Livewire v3 结果: {result}")
            results.append(result)
    except Exception as e:
        log.warning(f"Livewire v3 调用失败: {e}")

    # 策略2: Livewire v2
    try:
        result = sb.execute_script(_LW_V2_JS)
        if result:
            log.info(f"Livewire v2 结果: {result}")
            results.append(result)
    except Exception as e:
        log.warning(f"Livewire v2 调用失败: {e}")

    # 策略3: 按钮点击
    try:
        result = sb.execute_script(_LW_CLICK_JS)
        if result:
            log.info(f"按钮点击结果: {result}")
            results.append(result)
    except Exception as e:
        log.warning(f"按钮点击失败: {e}")

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
def run_single_server(sb, server_num: str, region: str) -> bool:
    """对一个服务器执行续期，返回是否成功"""
    url_app = f"{SITE_URL.rstrip('/')}/servers/{server_num}"

    log.info("=" * 40)
    log.info(f"🚀 开始续期 [{region}] ({server_num})")

    try:
        proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
        ip_val = requests.get(
            "https://api.ipify.org?format=json",
            proxies=proxies, timeout=10
        ).json().get("ip", "Unknown")
        log.info(f"🌐 当前出口 IP: {ip_val}")
    except Exception:
        log.warning("⚠️ 无法获取出口 IP，跳过")

    log.info(f"📂 正在进入续期面板 [{region}] ...")
    try:
        sb.uc_open_with_reconnect(url_app, reconnect_time=5)
        human_wait(8, 12)
    except Exception as e:
        raise Exception(f"打开面板失败: {e}")

    # 检查登录状态
    current_url = sb.get_current_url().lower()
    if "login" in current_url:
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

    # 获取续期前时间
    timestamp_before = "未知"
    try:
        sb.wait_for_element_visible('#sd-timer', timeout=15)
        timestamp_before = sb.get_text('#sd-timer').strip()
    except Exception:
        pass
    log.info(f"🕒 续期前剩余运行时间: {timestamp_before}")

    # 滚动到底部找到按钮
    try:
        ActionChains(sb.driver).scroll_by_amount(0, 600).perform()
        human_wait(2, 4)
    except Exception:
        pass

    # 点击续期按钮
    try:
        log.info("🖱️ 正在点击 'VOTE + ADD 90 MIN'...")
        sb.wait_for_element_visible("#sd-vote-btn", timeout=10)
        sb.click('#sd-vote-btn')
    except Exception as e:
        # 备用：Livewire 直接调用
        log.warning("按钮点击失败，尝试 Livewire extend...")
        lw_result = livewire_extend(sb)
        if not lw_result["success"]:
            raise Exception(f"未找到续期按钮: {e}")

    # 破解 Turnstile
    human_wait(2, 4)
    bypass_turnstile(sb)

    # 点击最终提交按钮
    try:
        log.info("🖱️ 正在点击最终提交按钮 'VOTE + ADD 90 MINUTES'...")
        sb.wait_for_element_visible("#vm-submit", timeout=15)
        sb.uc_click('#vm-submit')
        human_wait(8, 12)
    except Exception as e:
        log.warning(f"提交按钮点击失败: {e}")
        # 再次尝试 Livewire
        livewire_extend(sb)
        human_wait(5, 8)

    time.sleep(5)

    # 获取续期后时间
    timestamp_after = "未知"
    try:
        timestamp_after = sb.get_text('#sd-timer').strip()
    except Exception:
        pass
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


def send_tg_with_photo(msg: str, photo_path: str = None):
    """带截图的 TG 通知"""
    if not (TG_TOKEN and TG_CHAT_ID):
        log.warning("TG 未配置，跳过通知")
        return
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            with open(photo_path, 'rb') as f:
                requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': msg},
                              files={'photo': f})
        else:
            requests.post(url, json={
                "chat_id": TG_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
            })
        log.info("✅ TG 通知发送成功")
    except Exception as e:
        log.warning(f"TG 通知失败: {e}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run():
    from seleniumbase import SB

    # 解析代理端口用于预检
    proxy_port = 1080
    if PROXY_URL:
        port_match = re.search(r':(\d+)$', PROXY_URL.rstrip('/'))
        proxy_port = int(port_match.group(1)) if port_match else 1080

    log.info("=" * 60)
    log.info("gaming4free 续期启动")
    log.info(f"代理地址: {PROXY_URL or '(未配置)'}")
    log.info(f"目标站点: {SITE_URL}")
    log.info(f"MC 用户:  {USERNAME or '(未配置)'}")
    log.info(f"服务器列表: {len(SERVER_LIST)} 个")
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

    # 启动浏览器
    CHROMIUM_ARGS = (
        "--no-sandbox,--disable-dev-shm-usage,--disable-gpu,"
        "--window-position=0,0,--window-size=1280,720,"
        "--disable-blink-features=AutomationControlled,"
        "--disable-infobars,--disable-popup-blocking,"
        "--disable-features=OptimizationGuideModelDownloading,"
        "OptimizationHintsFetching,OptimizationTargetPrediction"
    )

    log.info(f"正在启动浏览器 (uc=True, headed=True, xvfb=True)...")
    with SB(
        browser="chrome",
        uc=True,
        test=True,
        headed=True,
        headless=False,
        xvfb=True,
        chromium_arg=CHROMIUM_ARGS,
        proxy=PROXY_URL if PROXY_URL else None,
    ) as sb:
        log.info("✅ 浏览器启动成功")
        sb.set_window_size(1280, 720)

        # 注入 cookie（如有）
        if COOKIE_STR:
            log.info("注入自定义 cookie...")
            for item in COOKIE_STR.split(";"):
                if "=" in item:
                    k, v = item.strip().split("=", 1)
                    try:
                        sb.set_cookie(k, v)
                    except Exception:
                        pass

        # 登录（如有用户名密码）
        if USERNAME:
            log.info(f"尝试登录用户: {USERNAME}")
            login_url = LOGIN_URL or SITE_URL
            sb.open(login_url)
            sb.sleep(2)

            user_selectors = [
                'input[name="username"]', 'input[name="user"]',
                'input[name="mc_username"]', 'input[type="text"]',
                'input[id*="user"]', 'input[name="email"]',
            ]
            pass_selectors = [
                'input[name="password"]', 'input[type="password"]',
            ]
            submit_selectors = [
                'button[type="submit"]', 'input[type="submit"]',
                'button:contains("Login")', 'button:contains("登录")',
                'button:contains("Sign in")',
            ]

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
                        human_wait(0.5, 1.2)
                        sb.click(sel, timeout=5)
                        log.info("登录表单已提交")
                        time.sleep(3)
                        break
                except Exception:
                    continue

        # 处理 CF 5 秒盾
        log.info("等待 CF 5 秒盾（如有）...")
        for _ in range(15):
            if "just a moment" in sb.get_text("body").lower():
                time.sleep(1)
            else:
                break

        # 如果有 SERVERS 配置，逐个续期
        if SERVER_LIST:
            success_count = 0
            fail_count = 0
            for server in SERVER_LIST:
                try:
                    if run_single_server(sb, server["num"], server["region"]):
                        success_count += 1
                    else:
                        fail_count += 1
                except Exception as e:
                    log.error(f"❌ [{server['region']}] 续期失败: {e}")
                    error_shot = screenshot(sb, f"error_{server['num']}")
                    tg(f"❌ [{server['region']}] 执行失败: {e}\n🖥️ 编号: {server['num']}")
                    fail_count += 1

            msg = (
                f"gaming4free 续期完成\n"
                f"成功: {success_count} | 失败: {fail_count}\n"
                f"总计: {len(SERVER_LIST)} 个服务器"
            )
            log.info(msg)
            tg(msg)
        else:
            # 无 SERVERS 配置，走旧版循环续期逻辑
            log.info("未配置 SERVERS，使用默认循环续期模式...")
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
                f"gaming4free 续期完成\n"
                f"成功点击: {click_count} 次\n"
                f"最终剩余: {h}h {m}m"
            )
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

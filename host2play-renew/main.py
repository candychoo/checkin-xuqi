#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
host2play 自动续期脚本（视频广告版）
===================================
- 使用 DrissionPage 自动化浏览器操作
- 支持代理配置（家宽代理 / WARP）
- 广告视频播放后自动续期
- 支持多账号批量续期
"""

import os
import sys
import time
import json
import random
import logging
import requests
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

# ==========================================================
# 配置
# ==========================================================
RENEW_URL = os.getenv("H2P_RENEW_URL", "")
COOKIE_STR = os.getenv("H2P_COOKIE", "")
WARP_PROXY = os.getenv("WARP_PROXY", "")
PROXY_URL = os.getenv("PROXY_URL", "")
RENEW_THRESHOLD_SECONDS = 25 * 3600  # 剩余超过25小时则跳过
PAGE_TIMEOUT = 180
TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
TZ_CN = timezone(timedelta(hours=8))


def now_cn():
    return datetime.now(TZ_CN)


ROOT = Path(__file__).parent
SHOT_DIR = ROOT / "output" / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("host2play")


# ==========================================================
# 工具函数
# ==========================================================

def tg(msg: str, silent: bool = False):
    prefix = "\U0001f3ae <b>host2play</b>\n"
    if "host2play" not in msg.lower():
        msg = prefix + msg
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
                "disable_notification": silent,
            },
            timeout=10,
        )
    except Exception:
        pass


def parse_expires(text: str) -> int:
    if not text:
        return -1
    m = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", text)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return -1


def get_server_info(page):
    server_id = "Unknown"
    expires_text = "Unknown"
    expires_sec = -1

    for attempt in range(10):
        try:
            text_content = page.run_js("return document.body.innerText")
            time_match = re.search(r"(\d{1,2}:\d{2}:\d{2})", text_content)
            if time_match:
                expires_text = time_match.group(1)
                expires_sec = parse_expires(expires_text)
                sid_match = re.search(r"Renew server:\s*([a-zA-Z0-9\-]+)", text_content, re.IGNORECASE)
                if sid_match:
                    server_id = sid_match.group(1)
                if server_id == "Unknown":
                    sid_match2 = re.search(r"Server:\s*([a-zA-Z0-9\-]+)", text_content, re.IGNORECASE)
                    if sid_match2:
                        server_id = sid_match2.group(1)
                if server_id != "Unknown":
                    break
        except Exception:
            pass
        time.sleep(2)

    return server_id, expires_text, expires_sec


def debug_dump(page, label=""):
    try:
        shot_path = SHOT_DIR / f"debug_{label}_{int(time.time())}.png"
        page.get_screenshot(path=str(shot_path))
        log.info(f"调试截图已保存: {shot_path}")
    except Exception:
        pass
    try:
        src_path = SHOT_DIR / f"debug_src_{label}_{int(time.time())}.txt"
        src_path.write_text(page.html[:20000], encoding="utf-8")
        log.info(f"页面源码已保存: {src_path} (前20000字符)")
    except Exception:
        pass


# ==========================================================
# 广告视频处理（新版）
# ==========================================================

def handle_ad_video(page):
    """处理广告视频 - 点击播放并等待完成"""
    log.info("等待广告播放器出现...")

    # 1. 等待播放器和大播放按钮出现
    play_btn = None
    video_area = None

    for wait in range(30):
        try:
            # 常见 Video.js 播放器大按钮
            play_btn = page.ele('css:.vjs-big-play-button', timeout=2)
            if play_btn:
                log.info("找到大播放按钮 (.vjs-big-play-button)")
                break
        except Exception:
            pass

        try:
            # 整个视频区域（兜底）
            video_area = page.ele('css:.video-js', timeout=2)
            if video_area:
                log.info("找到视频区域 (.video-js)")
                break
        except Exception:
            pass

        try:
            # 直接找 <video> 元素（有些页面还是用原生 video）
            video = page.ele('tag:video', timeout=2)
            if video:
                log.info("找到 <video> 元素")
                video_area = video
                break
        except Exception:
            pass

        time.sleep(1)

    # 2. 点击播放按钮或视频区域
    try:
        if play_btn:
            log.info("点击大播放按钮...")
            play_btn.click()
        elif video_area:
            log.info("点击视频区域开始播放...")
            video_area.click()
        else:
            log.warning("未找到播放按钮或视频区域，尝试用 JS 播放")
            try:
                page.run_js("var v=document.querySelector('video');if(v){v.play();}")
                log.info("通过 JS 播放 video 元素")
            except Exception:
                log.warning("JS 播放失败，继续后续流程")
    except Exception as e:
        log.warning(f"点击播放失败: {e}")

    # 3. 等待广告播放完成或出现跳过按钮
    log.info("等待广告视频播放...")
    for check in range(90):  # 最多等 90 秒
        try:
            # 检查 video 是否结束
            ended = page.run_js("var v=document.querySelector('video');return v&&v.ended;")
            if ended:
                log.info("检测到 video 播放结束")
                return True
        except Exception:
            pass

        # 有些广告会出现 Skip 按钮
        try:
            skip_btn = page.ele('css:button:contains("Skip")', timeout=1)
            if skip_btn:
                log.info("找到跳过按钮，点击跳过")
                skip_btn.click()
                time.sleep(3)
                return True
        except Exception:
            pass

        # 有些广告结束后会隐藏播放器或显示“已完成”字样
        try:
            text_content = page.run_js("return document.body.innerText")
            if "ad finished" in text_content.lower() or "advertisement finished" in text_content.lower():
                log.info("检测到广告完成提示文本")
                return True
        except Exception:
            pass

        time.sleep(1)

    log.warning("视频播放超时，继续执行（可能已完成或站点未返回状态）")
    return True


def inject_cookies(page, cookie_str: str):
    if not cookie_str:
        return
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            try:
                page.set.cookies({k.strip(): v.strip()})
            except Exception:
                pass


def create_proxy_auth_extension(proxy_url):
    import zipfile

    if "@" not in proxy_url:
        return None
    try:
        base_url = proxy_url.split("?")[0]
        scheme = base_url.split("://")[0] if "://" in base_url else "http"
        content = base_url.split("://")[1] if "://" in base_url else base_url
        auth_part, addr_part = content.split("@")
        proxy_user, proxy_pass = auth_part.split(":")
        addr_split = addr_part.split(":")
        proxy_host = addr_split[0]
        proxy_port = addr_split[1] if len(addr_split) > 1 else ("1080" if "socks" in scheme else "8080")
        scheme = "socks5" if "socks" in scheme else "http"
        log.info(f"代理解析: {scheme}://{proxy_host}:{proxy_port}")
    except Exception:
        return None

    manifest_json = json.dumps(
        {
            "version": "1.0.0",
            "manifest_version": 2,
            "name": "Chrome Proxy",
            "permissions": [
                "proxy",
                "tabs",
                "unlimitedStorage",
                "storage",
                "<all_urls>",
                "webRequest",
                "webRequestBlocking",
            ],
            "background": {"scripts": ["background.js"]},
            "minimum_chrome_version": "22.0.0",
        }
    )
    background_js = """
    var config = { mode: "fixed_servers", rules: { singleProxy: { scheme: "%s", host: "%s", port: parseInt(%s) }, bypassList: ["localhost"] } };
    chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
    chrome.webRequest.onAuthRequired.addListener(function(details) {
        return { authCredentials: { username: "%s", password: "%s" } };
    }, {urls: ["<all_urls>"]}, ['blocking']);
    """ % (scheme, proxy_host, proxy_port, proxy_user, proxy_pass)

    plugin_path = ROOT / "proxy_auth_plugin.zip"
    with zipfile.ZipFile(str(plugin_path), "w") as zp:
        zp.writestr("manifest.json", manifest_json)
        zp.writestr("background.js", background_js)
    return str(plugin_path)


# ==========================================================
# 单账号续期
# ==========================================================

def run_one(label: str, renew_url: str, cookie_str: str):
    """执行单个账号的续期"""
    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.headless()
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--disable-extensions")
    co.set_argument("--disable-background-timer-throttling")
    co.set_argument("--disable-renderer-backgrounding")
    co.set_argument("--disable-backgrounding-occluded-windows")
    co.set_user_agent(
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    if PROXY_URL:
        if "@" in PROXY_URL:
            plugin = create_proxy_auth_extension(PROXY_URL)
            if plugin:
                co.add_extension(plugin)
            else:
                co.set_argument(f"--proxy-server={PROXY_URL}")
        else:
            co.set_argument(f"--proxy-server={PROXY_URL}")
    elif WARP_PROXY:
        co.set_argument(f"--proxy-server={WARP_PROXY}")

    page = ChromiumPage(co)
    page.set.timeouts(PAGE_TIMEOUT)

    try:
        # 检查出口 IP
        try:
            page.get("https://api.ip.sb/ip", timeout=15)
            ip = page.run_js("return document.body.innerText").strip()
            log.info(f"当前出口 IP: {ip}")
        except Exception as e:
            log.warning(f"IP检查失败: {e}")

        log.info(f"正在访问: {renew_url}")
        page.get(renew_url)
        time.sleep(5)

        # 注入 Cookie
        if cookie_str:
            log.info("注入 Cookie...")
            inject_cookies(page, cookie_str)
            page.get(renew_url)
            time.sleep(10)

        # 获取服务器信息
        server_id, old_time, old_sec = get_server_info(page)
        log.info(f"账号: {label} | 服务器: {server_id} | 剩余: {old_time} ({old_sec}秒)")

        # 如果剩余时间足够，跳过
        if old_sec > RENEW_THRESHOLD_SECONDS:
            return {
                "label": label,
                "sid": server_id,
                "ok": True,
                "msg": f"跳过 (剩余 {old_sec // 3600}h)",
                "new": f"{old_sec // 3600}h",
            }

        # 查找续期按钮
        renew_btn = None
        btn_texts = ["Renew server", "Renew", "Extend", "Continue"]
        for btn_text in btn_texts:
            try:
                renew_btn = page.ele(f"text:{btn_text}", timeout=5)
                if renew_btn:
                    log.info(f"找到续期按钮: text:{btn_text}")
                    break
            except Exception:
                pass

        if not renew_btn:
            try:
                renew_btn = page.ele('css:button.purple', timeout=5)
                if renew_btn:
                    log.info("找到紫色续期按钮")
            except Exception:
                pass

        if not renew_btn:
            try:
                renew_btn = page.ele('xpath://button[contains(text(), "Renew")]', timeout=5)
                if renew_btn:
                    log.info("找到续期按钮 (xpath)")
            except Exception:
                pass

        if not renew_btn:
            page.get_screenshot(path=str(SHOT_DIR / f"error_{label}_no_btn.png"))
            log.error("未找到续期按钮")
            debug_dump(page, "no_renew_btn")
            return {"label": label, "sid": server_id, "ok": False, "msg": "未找到按钮"}

        log.info("点击续期按钮...")
        renew_btn.click()
        time.sleep(3)

        # 处理广告视频
        ad_handled = handle_ad_video(page)

        if ad_handled:
            log.info("广告视频处理完成，等待续期确认...")
            time.sleep(5)

            # 保存状态
            debug_dump(page, "after_ad")

            # 刷新页面同步状态
            log.info("刷新页面同步状态...")
            page.get(renew_url)
            time.sleep(10)

            # 检查新时间
            _, new_time, new_sec = get_server_info(page)
            log.info(f"新时间: {new_time} ({new_sec}秒), 旧时间: {old_time} ({old_sec}秒)")

            if new_sec > old_sec:
                return {
                    "label": label,
                    "sid": server_id,
                    "ok": True,
                    "old": old_time,
                    "new": new_time,
                }
            else:
                log.warning(f"时间未增加: {old_sec} -> {new_sec}")
                debug_dump(page, "time_not_increased")
                return {"label": label, "sid": server_id, "ok": False, "msg": f"时间未增加 ({old_sec}s -> {new_sec}s)"}
        else:
            log.warning("广告视频处理失败")
            return {"label": label, "sid": server_id, "ok": False, "msg": "广告视频处理失败"}

    except Exception as e:
        log.error(f"运行异常: {e}")
        return {"label": label, "sid": "Error", "ok": False, "msg": f"异常: {e}"}
    finally:
        try:
            page.quit()
        except Exception:
            pass


# ==========================================================
# 多账号收集 & 主运行
# ==========================================================

def collect_accounts():
    """收集账号配置"""
    accounts = []
    multi = os.getenv("H2P_ACCOUNTS", "").strip()
    if multi:
        for line in multi.splitlines():
            parts = line.strip().split("|||")
            if len(parts) >= 3:
                accounts.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))

    if not accounts and RENEW_URL and COOKIE_STR:
        accounts.append(("main", RENEW_URL, COOKIE_STR))

    return accounts


def run():
    """主运行函数"""
    accounts = collect_accounts()
    if not accounts:
        log.error("未找到任何账号配置")
        return False

    results = []
    for label, url, ck in accounts:
        log.info(f"========== 开始处理账号: {label} ==========")
        results.append(run_one(label, url, ck))
        time.sleep(random.uniform(5, 10))

    ok_count = sum(1 for r in results if r.get("ok"))
    summary = [
        "\U0001f3ae <b>host2play 续期</b>",
        f"\u23f0 {now_cn():%Y-%m-%d %H:%M:%S}",
        "",
        f"\U0001f4ca 总账号: {len(results)} | \u2705 {ok_count} | \u274c {len(results) - ok_count}",
        "",
    ]
    default_msg = "成功"
    for r in results:
        status = "\u2705" if r.get("ok") else "\u274c"
        if r.get("new"):
            summary.append(f"\U0001f464 <b>{r['label']}</b> ({r.get('sid', 'Unknown')}): {status} {r['new']}")
        else:
            summary.append(f"\U0001f464 <b>{r['label']}</b> ({r.get('sid', 'Unknown')}): {status} {r.get('msg', default_msg)}")

    tg("\n".join(summary))
    return all(r.get("ok") for r in results)


if __name__ == "__main__":
    if run():
        sys.exit(0)
    else:
        sys.exit(1)

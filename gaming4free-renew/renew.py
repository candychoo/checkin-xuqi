# -*- coding: utf-8 -*-
import os, sys, time, json, traceback, re
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from util import *
from util import _LW_DIAGNOSE_JS, _LW_EXTEND_V3_JS, _LW_V2_JS, _LW_CLICK_JS
from cfg import *
from tg import send_tg

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def init_browser(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1920,1080")
    opts.page_load_strategy = "eager"
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    opts.add_argument(f"--user-agent={ua}")
    cd = "/usr/bin/chromedriver"
    for p in ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver", "/opt/chrome/chromedriver"]:
        if os.path.exists(p):
            cd = p
            break
    svc = Service(executable_path=cd)
    dr = webdriver.Chrome(service=svc, options=opts)
    dr.set_page_load_timeout(120)
    try:
        dr.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except:
        pass
    return dr

def inject_cookie(dr, cookie_str):
    pairs = [p.strip() for p in cookie_str.split(";")]
    for p in pairs:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                dr.add_cookie({"name": k.strip(), "value": v.strip(), "domain": ".gaming4free.net", "path": "/"})
            except:
                pass

def get_time(dr):
    for attempt in range(3):
        try:
            elements = dr.find_elements(By.XPATH, "//*[contains(text(),'remaining')]")
            if not elements:
                elements = dr.find_elements(By.XPATH, "//*[contains(text(),'Remaining')]")
            if elements:
                for el in elements:
                    txt = el.text.strip() or el.get_attribute("textContent").strip()
                    if not txt:
                        continue
                    txt_clean = re.sub(r'(?i)remaining', '', txt).strip()
                    m = re.search(r'(\d{1,2}:\d{2}:\d{2})', txt_clean)
                    if m:
                        time_str = m.group(1)
                        parts = time_str.split(':')
                        total = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
                        log(f"✅ remaining 行: {time_str} (行: {txt[:50]})")
                        return time_str, total
                    m2 = re.search(r'(\d{1,2}:\d{2})', txt_clean)
                    if m2:
                        time_str = m2.group(1)
                        parts = time_str.split(':')
                        total = int(parts[0])*60 + int(parts[1])
                        log(f"✅ remaining 行: {time_str} (行: {txt[:50]})")
                        return time_str, total
            body_text = dr.execute_script("return document.body ? document.body.innerText : '';")
            for pat in [r'(\d{1,2}:\d{2}:\d{2})\s*remaining', r'remaining[^\d]*(\d{1,2}:\d{2}:\d{2})', r'(\d{1,2}:\d{2}:\d{2})\n?remaining']:
                m = re.search(pat, body_text, re.IGNORECASE)
                if m:
                    time_str = m.group(1)
                    parts = time_str.split(':')
                    total = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
                    log(f"✅ remaining 行: {time_str} (正则匹配)")
                    return time_str, total
            all_times = re.findall(r'(\d{1,2}:\d{2}:\d{2})', body_text)
            if all_times:
                time_str = all_times[-1]
                parts = time_str.split(':')
                total = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
                log(f"✅ remaining 行: {time_str} (全文匹配, 候选: {len(all_times)})")
                return time_str, total
        except Exception as e:
            if attempt == 2:
                log(f"⚠️ 获取剩余时间失败: {e}")
                try:
                    dr.refresh()
                    time.sleep(3)
                except:
                    pass
            time.sleep(2)
    return None, 0

def scr(dr, name):
    try:
        os.makedirs("debug_output", exist_ok=True)
        dr.save_screenshot(f"debug_output/{name}.png")
    except:
        pass

def find_button(dr):
    return dr.execute_script("""
        var allEls = document.querySelectorAll('button, a, [role="button"]');
        for (var i = 0; i < allEls.length; i++) {
            var el = allEls[i];
            var t = (el.innerText || el.textContent || '').trim();
            if (t.indexOf('90') !== -1 && t.indexOf('min') !== -1) {
                var rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    return t.substring(0, 50);
                }
            }
        }
        return '';
    """)

def click_button(dr):
    return dr.execute_script("""
        var allEls = Array.from(document.querySelectorAll('button, a, [role="button"]'));
        for (var i = 0; i < allEls.length; i++) {
            var el = allEls[i];
            var t = (el.innerText || el.textContent || '').trim();
            if (t.indexOf('90') !== -1 && t.indexOf('min') !== -1) {
                var rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && !el.disabled) {
                    el.scrollIntoView({block: 'center'});
                    el.click();
                    return 'clicked:' + el.tagName + ':' + t.substring(0, 30);
                }
            }
        }
        return 'not_found';
    """)

def wait_for_cooldown(dr, max_wait=600):
    waited = 0
    interval = 30
    while waited < max_wait:
        time.sleep(interval)
        waited += interval
        try:
            dr.refresh()
            time.sleep(3)
        except:
            pass
        btn = find_button(dr)
        if btn and ('watch ad' in btn.lower() or 'watch' in btn.lower()):
            log(f"✅ 按钮已恢复: {btn}")
            return True
        log(f"⏳ 仍在冷却... 已等 {waited}s, 按钮: {btn}")
    return False

def do_rounds(dr, sn, su, max_rounds=10):
    cr = 0
    while cr < max_rounds:
        cr += 1
        log(f"\n🔄 --- 第 {cr}/{max_rounds} 轮续期 ---")

        bl, bs = get_time(dr)
        if not bl:
            log("⚠️ 无法获取剩余时间，刷新重试")
            try: dr.refresh(); time.sleep(5)
            except: pass
            continue
        log(f"⏱️ 当前剩余时长: {bl} ({bs}秒)")

        pre_time = bs
        pre_ts = time.time()

        # 查找按钮并检测状态
        btn_text = find_button(dr)
        if not btn_text:
            log("❌ 未找到 +90min 按钮!")
            scr(dr, f"fail_round{cr}_no_btn")
            time.sleep(10)
            try: dr.refresh(); time.sleep(5)
            except: pass
            continue

        has_watch_ad = 'watch ad' in btn_text.lower() or 'watch' in btn_text.lower()
        log(f"🔍 按钮: {btn_text}, watch_ad={has_watch_ad}")

        # 按钮在冷却中，等待恢复
        if not has_watch_ad:
            log("⏳ 按钮不在 watch ad 状态，正在冷却，等待恢复...")
            try:
                cd_text = dr.execute_script("""
                    var bodyText = document.body ? document.body.innerText : '';
                    var patterns = [/next.*?(\d{1,2}):(\d{2})/i, /cooldown.*?(\d{1,2}):(\d{2})/i, /wait.*?(\d{1,2}):(\d{2})/i];
                    for (var i = 0; i < patterns.length; i++) {
                        var m = bodyText.match(patterns[i]);
                        if (m) return m[0];
                    }
                    return '';
                """)
                if cd_text:
                    log(f"⏳ 冷却信息: {cd_text}")
            except: pass

            recovered = wait_for_cooldown(dr, max_wait=600)
            if not recovered:
                log("❌ 等待冷却超时，跳过本轮")
                continue
            bl, bs = get_time(dr)
            pre_time = bs
            log(f"⏱️ 冷却后剩余: {bl} ({bs}秒)")

        # 点击按钮
        click_result = click_button(dr)
        log(f"🖱️ 点击: {click_result}")

        if click_result == 'not_found':
            log("❌ 点击时按钮消失了")
            time.sleep(10)
            try: dr.refresh(); time.sleep(5)
            except: pass
            continue

        # ===== 检测 Turnstile 验证弹窗 =====
        log("⏳ 等待 Turnstile 验证...")
        for _ in range(60):  # 最多等 3 分钟
            time.sleep(3)
            # 检查 Turnstile 是否消失（通过检查 iframe）
            turnstile_iframe = dr.execute_script("""
                var iframes = document.querySelectorAll('iframe');
                for (var i = 0; i < iframes.length; i++) {
                    if (iframes[i].src && (iframes[i].src.indexOf('turnstile') !== -1 || iframes[i].src.indexOf('challenges.cloudflare.com') !== -1)) {
                        return 'present';
                    }
                }
                return 'gone';
            """)
            if turnstile_iframe == 'gone':
                log("✅ Turnstile 验证通过")
                break
            # 检查时间是否已增加（提前跳出）
            ct, cs = get_time(dr)
            if ct:
                diff = int(cs) - int(pre_time)
                if diff > 300:
                    log(f"✅ 检测到时间增加 → {ct}, +{diff}秒 (Turnstile 前)")
                    break
        else:
            log("⚠️ Turnstile 等待超时")

            (By.XPATH, "//button[contains(text(), 'Confirm')]"),
            (By.XPATH, "//button[contains(text(), 'Yes')]"),
            (By.XPATH, "//button[contains(text(), 'OK')]"),
            (By.XPATH, "//button[contains(text(), 'Renew')]"),
            (By.XPATH, "//button[contains(text(), 'Extend')]"),
            (By.CSS_SELECTOR, ".swal2-confirm"),
            (By.CSS_SELECTOR, ".modal-footer button"),
        ]
        for by, sel in confirm_selectors:
            try:
                confirm_btn = WebDriverWait(dr, 2).until(EC.element_to_be_clickable((by, sel)))
                confirm_btn.click()
                log(f"✅ 处理确认弹窗: {sel}")
                break
            except: continue

        # 检测 alert
        try:
            alert = dr.switch_to.alert
            log(f"⚠️ 检测到 Alert: {alert.text}")
            alert.accept()
        except: pass

        # 等待续期生效 (30s)
        log("⏳ 等待续期生效 (最长 30s)...")
        wait_end = time.time() + 30
        while time.time() < wait_end:
            try:
                ct, cs = get_time(dr)
                if ct:
                    diff = int(cs) - int(pre_time)
                    if diff > 300:
                        log(f"✅ 检测到时间增加 → {ct}, +{diff}秒")
                        break
            except: pass
            time.sleep(3)

        # 最终判断
        al, as_ = get_time(dr)
        df = int(as_) - int(pre_time) if as_ else 0
        elapsed = time.time() - pre_ts
        log(f"⏱️ 续期后: {al} ({as_}秒), 增加: {df}秒, 耗时: {elapsed:.0f}s")

        if df > 300:
            log(f"🎉 续期成功! +{df}s ({bl} → {al})")
            try: send_tg(f"🎉 [{sn}] Pro续期成功 (+{df//60}分钟)", sn, al)
            except: pass
            log("💤 等待30秒再续下一轮...")
            time.sleep(30)
            try: dr.refresh(); time.sleep(5)
            except: pass
            continue
        else:
            scr(dr, f"fail_round{cr}")
            try:
                err_text = dr.execute_script("return document.body?document.body.innerText.substring(0,500):'';")
                if err_text:
                    log(f"⚠️ 页面内容片段: {err_text[:300]}")
            except: pass
            log(f"❌ 续期失败，继续下一轮")
            time.sleep(10)
            try: dr.refresh(); time.sleep(5)
            except: pass
            continue

    return False

def main():
    log("========== 开始处理服务器账号 (Pro v31) ==========")
    cookie = os.environ.get("G4F_COOKIE") or os.environ.get("GAME4FREE_COOKIE", "")
    server_url = os.environ.get("G4F_SERVER_URL") or os.environ.get("GAME4FREE_RENEW_URL", "")
    server_name = os.environ.get("G4F_SERVER_NAME") or os.environ.get("GAME4FREE_ACCOUNT", "gaming4free")
    if not cookie or not server_url:
        log("❌ 缺少环境变量 G4F_COOKIE 或 G4F_SERVER_URL")
        sys.exit(1)

    for attempt in range(3):
        log(f"🚀 启动浏览器 (第 {attempt + 1}/3 次尝试)...")
        dr = None
        try:
            dr = init_browser(headless=True)
            log(f"🌐 访问页面: {server_url}")
            try: dr.get("https://gaming4free.net/login")
            except Exception as e: log(f"⚠️ 登录页加载超时，继续")
            time.sleep(3)
            log("🍪 注入 Cookie...")
            inject_cookie(dr, cookie)
            log("⏳ 等待页面加载...")
            try: dr.get(server_url)
            except Exception as e: log(f"⚠️ 页面加载超时(正常)，继续执行")
            time.sleep(5)
            try:
                WebDriverWait(dr, 30).until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'remaining')]")))
            except: log("⚠️ 等待按钮超时，尝试继续...")
            title = dr.title
            log(f"📄 标题: {title}")
            if "Login" in title:
                log("❌ Cookie 失效，仍在登录页")
                dr.quit()
                sys.exit(1)
            result = do_rounds(dr, server_name, server_url, max_rounds=10)
            dr.quit()
            return
        except Exception as e:
            log(f"❌ 异常: {e}")
            log(traceback.format_exc())
            if dr:
                try: scr(dr, f"error_attempt{attempt}")
                except: pass
                try: dr.quit()
                except: pass
            time.sleep(10)
    log("❌ 3次尝试均失败")

if __name__ == "__main__":
    main()

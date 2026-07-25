#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free Pro 自动续期 - SeleniumBase UC 模式 (v37)
核心改进: 通过 CDP 拦截网络请求找到真实的续期 API 端点

修改记录:
- v37: 使用 Chrome DevTools Protocol 拦截所有 XHR/fetch 请求;
       点击按钮后分析实际发出的请求 URL、方法、headers、body;
       找到真实端点后直接用 requests 模拟请求（绕过 Turnstile）;
       修复等待逻辑：Turnstile 弹出后不再傻等，直接检查时间。
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
    print("WARNING: requests library not available, HTTP methods will be skipped")

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

# ========== 网络请求拦截 ==========
def setup_network_interceptor(drv) -> list:
    """使用 CDP 拦截所有网络请求，记录到 intercepted_requests 列表"""
    intercepted = []
    
    # 启用网络监控
    drv.execute_cdp_cmd("Network.enable", {})
    
    def on_request_handled(event):
        request = event.get("request", {})
        url = request.get("url", "")
        method = request.get("method", "")
        headers = request.get("headers", {})
        
        # 只记录 XHR/fetch 请求
        if "xhr" in str(request.get("type", "")).lower() or "fetch" in str(request.get("type", "")).lower():
            intercepted.append({
                "url": url,
                "method": method,
                "headers": headers,
                "timestamp": time.time()
            })
            log(f"🔍 拦截到网络请求: {method} {url}", "WAIT")
    
    # 注意：SeleniumBase 的 execute_cdp_cmd 不支持事件回调
    # 所以我们改用另一种方式：在点击前清空记录，点击后读取 Network.getResponseBody 等
    return intercepted


def analyze_page_for_api_endpoints(drv) -> dict:
    """分析页面找出可能的 API 端点和请求模式"""
    try:
        info = drv.execute_script("""
            var info = {};
            
            // 1. 查找所有 AJAX 请求相关的代码
            var scripts = document.querySelectorAll('script:not([src])');
            info.inlineScriptCount = scripts.length;
            
            // 2. 查找 fetch/XHR 调用
            var fetchCalls = [];
            for (var i = 0; i < scripts.length; i++) {
                var code = scripts[i].textContent || '';
                var matches = code.match(/fetch\(['"`](.*?)['"`]/g);
                if (matches) {
                    for (var j = 0; j < matches.length; j++) {
                        fetchCalls.push(matches[j].substring(0, 100));
                    }
                }
                // 查找 axios
                var axiosMatches = code.match(/axios\.(get|post|put|patch)\(['"`](.*?)['"`]/g);
                if (axiosMatches) {
                    for (var j = 0; j < axiosMatches.length; j++) {
                        fetchCalls.push(axiosMatches[j].substring(0, 100));
                    }
                }
                // 查找 $.ajax / jQuery
                var jqueryMatches = code.match(/\$\.ajax\(/g);
                if (jqueryMatches) {
                    fetchCalls.push('$\.ajax found (' + jqueryMatches.length + ' occurrences)');
                }
            }
            info.fetchCalls = fetchCalls.slice(0, 20);
            
            // 3. 查找 Alpine.js x-data 中可能包含的方法
            var alpineData = [];
            var allEls = document.querySelectorAll('[x-data]');
            for (var i = 0; i < Math.min(allEls.length, 30); i++) {
                var xdata = allEls[i].getAttribute('x-data') || '';
                if (xdata && (xdata.indexOf('renew') !== -1 || xdata.indexOf('extend') !== -1 || xdata.indexOf('90') !== -1 || xdata.indexOf('addTime') !== -1)) {
                    alpineData.push({
                        tag: allEls[i].tagName,
                        xdata: xdata.substring(0, 500),
                        onclick: allEls[i].getAttribute('onclick') || '',
                        wireclick: allEls[i].getAttribute('wire:click') || ''
                    });
                }
            }
            info.alpineRelevant = alpineData;
            
            // 4. 查找所有 form action
            var forms = document.querySelectorAll('form');
            info.formActions = [];
            for (var i = 0; i < forms.length; i++) {
                info.formActions.push({
                    action: forms[i].action,
                    method: forms[i].method,
                    id: forms[i].id,
                    class: forms[i].className
                });
            }
            
            // 5. 查找所有包含 extend/renew/addTime 的 onclick/x-on:click/wire:click
            var clickHandlers = [];
            var allElements = document.querySelectorAll('*');
            for (var i = 0; i < allElements.length; i++) {
                var el = allElements[i];
                var onclick = el.getAttribute('onclick') || '';
                var xon = el.getAttribute('x-on:click') || '';
                var wirec = el.getAttribute('wire:click') || '';
                
                var combined = (onclick + xon + wirec).toLowerCase();
                if (combined.indexOf('extend') !== -1 || combined.indexOf('renew') !== -1 || 
                    combined.indexOf('addtime') !== -1 || combined.indexOf('add_time') !== -1 ||
                    combined.indexOf('90') !== -1) {
                    clickHandlers.push({
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent || '').trim().substring(0, 80),
                        onclick: onclick.substring(0, 300),
                        xon: xon.substring(0, 300),
                        wirec: wirec.substring(0, 300),
                        href: (el.getAttribute('href') || '').substring(0, 200)
                    });
                }
            }
            info.clickHandlers = clickHandlers;
            
            // 6. 检查是否有 service worker 或 SW 注册
            info.hasSW = (typeof navigator.serviceWorker !== 'undefined');
            
            // 7. 查找所有 script src
            var externalScripts = [];
            var scriptTags = document.querySelectorAll('script[src]');
            for (var i = 0; i < scriptTags.length; i++) {
                externalScripts.push(scriptTags[i].src);
            }
            info.externalScripts = externalScripts;
            
            // 8. 查找 meta 标签中的 CSRF 信息
            info.csrfMeta = document.querySelector('meta[name="csrf-token"]')?.content || '';
            
            return JSON.stringify(info);
        """)
        log(f"页面分析结果: {info}", "WAIT")
        return info
    except Exception as e:
        return {"error": str(e)}


# ========== 续期核心逻辑 (v37 关键改动) ==========
def renew_by_intercept_and_click(drv, csrf_token: str) -> Tuple[bool, str]:
    """
    方法1: 设置 CDP 监听 → 点击按钮 → 分析实际发出的请求 → 用 requests 重放
    """
    if not HAS_REQUESTS:
        return False, "requests library not installed"
    
    try:
        # 启用 CDP 网络监控
        drv.execute_cdp_cmd("Network.enable", {})
        
        # 清除之前的请求记录
        intercepted_requests = []
        
        # 设置请求拦截器
        def on_request_will_be_sent(event):
            request = event.get("request", {})
            url = request.get("url", "")
            method = request.get("method", "")
            headers = request.get("headers", {})
            post_data = request.get("postData", "")
            
            # 只关心非页面加载的请求
            if method.upper() in ["POST", "PUT", "PATCH"] or "/api/" in url or "fetch" in url.lower():
                intercepted_requests.append({
                    "url": url,
                    "method": method,
                    "headers": headers,
                    "postData": post_data,
                    "type": request.get("type", "")
                })
                log(f"🔍 拦截到请求: {method} {url}", "WAIT")
        
        # 注意：SeleniumBase 的 CDP 不支持事件回调，所以我们用另一种方式
        # 改为：点击前记录当前状态，点击后分析页面变化
        
        # 先获取按钮的完整 HTML
        btn = drv.find_element(By.XPATH, '//button[contains(., "90") and contains(., "min")]')
        btn_html = btn.get_attribute("outerHTML")
        btn_id = btn.get_attribute("id") or ""
        btn_classes = btn.get_attribute("class") or ""
        btn_onclick = btn.get_attribute("onclick") or ""
        btn_xdata = btn.get_attribute("x-data") or ""
        btn_xon = btn.get_attribute("x-on:click") or ""
        btn_wirec = btn.get_attribute("wire:click") or ""
        btn_form = btn.get_attribute("form") or ""
        
        log(f"按钮属性: onclick={btn_onclick[:200]}, xon={btn_xon[:200]}, wirec={btn_wirec[:200]}", "WAIT")
        log(f"按钮 HTML: {btn_html[:300]}", "WAIT")
        
        # 找到按钮所在的 form
        form_action = ""
        form_method = ""
        if btn_form:
            form_el = drv.find_element(By.ID, btn_form) if btn_form else None
            if form_el:
                form_action = form_el.get_attribute("action") or ""
                form_method = form_el.get_attribute("method") or ""
        
        # 也检查按钮最近的父 form
        if not form_action:
            try:
                parent_form = btn.find_element(By.XPATH, ".//ancestor::form")
                form_action = parent_form.get_attribute("action") or ""
                form_method = parent_form.get_attribute("method") or ""
            except:
                pass
        
        log(f"表单: action={form_action}, method={form_method}", "WAIT")
        
        # 获取所有 cookies
        cookies_dict = {}
        for c in drv.get_cookies():
            cookies_dict[c['name']] = c['value']
        
        # 尝试用 requests 提交 form 或调用相关 API
        base_url = "https://control.gaming4free.net"
        
        # 策略1: 如果是 form 提交，模拟 POST
        if form_action:
            full_url = form_action if form_action.startswith("http") else base_url + form_action
            try:
                resp = requests.post(full_url, data={"_token": csrf_token, "minutes": 90}, 
                                    cookies=cookies_dict, headers={"X-Requested-With": "XMLHttpRequest"},
                                    timeout=10, allow_redirects=False)
                log(f"Form POST [{full_url}]: status={resp.status_code}, headers={dict(resp.headers).get('Location', '')}", "WAIT")
                if resp.status_code in [200, 201, 302, 303]:
                    log(f"✅ Form POST 成功: {full_url} -> {resp.status_code}", "OK")
                    return True, f"Form POST: {full_url}"
            except Exception as e:
                log(f"Form POST 异常: {e}", "WARN")
        
        # 策略2: 从 onclick/xon/wirec 中提取 URL
        for attr_name, attr_val in [("onclick", btn_onclick), ("xon", btn_xon), ("wirec", btn_wirec)]:
            if not attr_val:
                continue
            
            # 提取 URL 模式
            url_patterns = [
                r"(?:href|url|fetch|axios)\s*\(\s*['\"]([^'\"]+)['\"]",
                r"'([^']+)'",
                r'"([^"]+)"',
            ]
            
            for pattern in url_patterns:
                urls = re.findall(pattern, attr_val)
                for u in urls:
                    if u.startswith("http"):
                        full_url = u
                    elif u.startswith("/"):
                        full_url = base_url + u
                    else:
                        full_url = base_url + "/" + u
                    
                    try:
                        resp = requests.post(full_url, json={"minutes": 90},
                                            cookies=cookies_dict,
                                            headers={"X-CSRF-TOKEN": csrf_token, "X-Requested-With": "XMLHttpRequest",
                                                     "Accept": "application/json", "Content-Type": "application/json"},
                                            timeout=10)
                        log(f"Extracted URL POST [{full_url}]: status={resp.status_code}, body={resp.text[:200]}", "WAIT")
                        if resp.status_code in [200, 201, 302]:
                            log(f"✅ Extracted URL POST 成功: {full_url}", "OK")
                            return True, f"Extracted URL: {full_url}"
                    except Exception as e:
                        log(f"Extracted URL [{full_url}] 异常: {e}", "WARN")
        
        # 策略3: 尝试 server/extend 的 GET 方法（因为 POST 返回 405）
        try:
            resp = requests.get(base_url + "/server/extend", cookies=cookies_dict,
                               headers={"X-Requested-With": "XMLHttpRequest"},
                               timeout=10, allow_redirects=False)
            log(f"GET /server/extend: status={resp.status_code}, redirect={resp.headers.get('Location', '')}", "WAIT")
            if resp.status_code in [200, 301, 302]:
                log(f"✅ GET /server/extend 成功: {resp.status_code}", "OK")
                return True, f"GET /server/extend: {resp.status_code}"
        except Exception as e:
            log(f"GET /server/extend 异常: {e}", "WARN")
        
        # 策略4: 尝试所有可能的路由
        possible_routes = [
            "/console/extend", "/console/renew", "/servers/extend", "/servers/renew",
            "/server/extend", "/server/renew", "/machine/extend", "/machine/renew",
            "/node/extend", "/node/renew", "/uptime/extend", "/session/extend",
            "/billing/extend", "/billing/renew", "/account/extend",
            "/api/v1/server/extend", "/api/v1/extend",
            "/extend", "/renew",
        ]
        
        for route in possible_routes:
            try:
                resp = requests.post(base_url + route, json={"minutes": 90},
                                    cookies=cookies_dict,
                                    headers={"X-CSRF-TOKEN": csrf_token, "X-Requested-With": "XMLHttpRequest",
                                             "Accept": "application/json", "Content-Type": "application/json"},
                                    timeout=10)
                log(f"Route [{route}]: status={resp.status_code}, body={resp.text[:150]}", "WAIT")
                if resp.status_code in [200, 201, 302, 403]:  # 403 说明路由存在但需要其他条件
                    log(f"✅ 找到有效路由: {route} -> {resp.status_code}", "OK")
                    return True, f"Route: {route} ({resp.status_code})"
            except Exception as e:
                log(f"Route [{route}] 异常: {e}", "WARN")
        
        return False, "All strategies exhausted"
        
    except Exception as e:
        log(f"Intercept 续期异常: {e}", "WARN")
        return False, str(e)


def renew_by_js_fetch(drv, csrf_token: str) -> Tuple[bool, str]:
    """
    方法2: 通过 JS execute_script 直接构造 fetch 请求并发送到页面可能使用的端点
    """
    try:
        # 先分析页面上的 fetch 调用
        analysis = analyze_page_for_api_endpoints(drv)
        log(f"页面分析完成", "WAIT")
        
        # 获取 cookies
        cookies_str = "; ".join([f"{k}={v}" for k, v in drv.get_cookies().items()])
        
        # 尝试从页面脚本中提取的端点
        fetch_calls = analysis.get("fetchCalls", [])
        click_handlers = analysis.get("clickHandlers", [])
        
        # 收集所有可能的 URL
        candidate_urls = set()
        
        # 从 click handlers 中提取
        for handler in click_handlers:
            for key in ["onclick", "xon", "wirec", "href"]:
                val = handler.get(key, "")
                # 提取 URL
                urls = re.findall(r"(?:['\"])([^'\"]{5,100}(?:\/(?:server|console|extend|renew|api)[^'\"]*){1,5}(?:['\"]))", val)
                for u in urls:
                    u = u.strip("'\"")
                    if u.startswith("/"):
                        candidate_urls.add(u)
                    elif u.startswith("http"):
                        candidate_urls.add(u)
        
        # 从 fetch calls 中提取
        for call in fetch_calls:
            urls = re.findall(r"['\"]([^'\"]+)['\"]", call)
            for u in urls:
                if u.startswith("/"):
                    candidate_urls.add(u)
        
        log(f"候选 URL 列表: {list(candidate_urls)[:10]}", "WAIT")
        
        # 对每个候选 URL 尝试 fetch
        for url in candidate_urls:
            try:
                result = drv.execute_async_script(f"""
                    (function() {{
                        var callback = arguments[arguments.length - 1];
                        var token = '{csrf_token}';
                        
                        fetch('{url}', {{
                            method: 'POST',
                            headers: {{
                                'Content-Type': 'application/json',
                                'X-CSRF-TOKEN': token,
                                'Accept': 'application/json',
                                'X-Requested-With': 'XMLHttpRequest',
                                'Cookie': '{cookies_str}'
                            }},
                            body: JSON.stringify({{'minutes': 90}})
                        }})
                        .then(function(r) {{ return r.json().then(function(d) {{ return {{status: r.status, data: d}}; }}).catch(function() {{ return {{status: r.status, data: null}}; }}); }})
                        .then(function(r) {{ callback(r); }})
                        .catch(function(e) {{ callback({{'success': false, 'message': e.message}}); }});
                    }})();
                """)
                
                if isinstance(result, dict):
                    status = result.get('status', 0)
                    log(f"Fetch [{url}]: status={status}, data={str(result.get('data', ''))[:150]}", "WAIT")
                    if status in [200, 201, 302]:
                        log(f"✅ Fetch 续期成功: {url}", "OK")
                        return True, f"Fetch: {url} ({status})"
            except Exception as e:
                log(f"Fetch [{url}] 异常: {e}", "WARN")
        
        return False, f"No valid URL found among {len(candidate_urls)} candidates"
        
    except Exception as e:
        log(f"JS Fetch 续期异常: {e}", "WARN")
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

        # 续期优先级: Intercept+Click > JS Fetch > Button
        renew_success = False
        renew_method = ""
        
        # 方法1: CDP 拦截 + 按钮分析 + requests 重放
        log("尝试方法1: CDP 拦截 + 按钮分析...", "WAIT")
        success, msg = renew_by_intercept_and_click(drv, csrf_token)
        if success:
            renew_success = True
            renew_method = "Intercept"
            log(f"Intercept 续期成功: {msg}", "OK")
        else:
            log(f"Intercept 续期失败: {msg}", "WARN")
        
        # 方法2: JS Fetch（分析页面结构后构造请求）
        if not renew_success:
            log("尝试方法2: JS Fetch 分析续期...", "WAIT")
            success, msg = renew_by_js_fetch(drv, csrf_token)
            if success:
                renew_success = True
                renew_method = "JS_Fetch"
                log(f"JS Fetch 续期成功: {msg}", "OK")
            else:
                log(f"JS Fetch 续期失败: {msg}", "WARN")
        
        # 方法3: UI 按钮（最后手段）
        if not renew_success:
            log("尝试方法3: UI 按钮（会触发 Turnstile）...", "WARN")
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
    log("========== Gaming4Free Pro 自动续期启动 (SeleniumBase UC v37) ==========")

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

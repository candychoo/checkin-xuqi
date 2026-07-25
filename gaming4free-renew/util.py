# -*- coding: utf-8 -*-
import os, sys, time, json, traceback
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def log(msg, level=""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = f"[{level}]" if level else ""
    print(f"[{ts}] {prefix} {msg}", flush=True)

def get_chromedriver():
    for p in ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver", "/opt/chrome/chromedriver"]:
        if os.path.exists(p):
            return p
    return "/usr/bin/chromedriver"

def init_browser(headless=True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    opts.add_argument(f"--user-agent={ua}")
    svc = Service(executable_path=get_chromedriver())
    dr = webdriver.Chrome(service=svc, options=opts)
    dr.set_page_load_timeout(60)
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
    """获取服务器剩余时长，返回 (HH:MM:SS, total_seconds)"""
    for attempt in range(3):
        try:
            el = WebDriverWait(dr, 10).until(
                EC.presence_of_element_located((By.XPATH, "//span[contains(text(),'remaining')]"))
            )
            txt = el.text.strip()
            txt_clean = txt.replace("remaining", "").strip()
            parts = txt_clean.split(":")
            if len(parts) >= 3:
                h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                total = h * 3600 + m * 60 + s
                log(f"✅ remaining 行: {txt_clean} (行: {txt})")
                return txt_clean, total
            elif len(parts) == 2:
                m, s = int(parts[0]), int(parts[1])
                total = m * 60 + s
                log(f"✅ remaining 行: {txt_clean} (行: {txt})")
                return txt_clean, total
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
    """截图保存到 debug_output/"""
    try:
        os.makedirs("debug_output", exist_ok=True)
        dr.save_screenshot(f"debug_output/{name}.png")
    except:
        pass

# ============================================================
# Livewire 点击常量 (v30-fix)
# ============================================================

_LW_EXTEND_V3_JS = """
(function(){
    try {
        var wireEls = document.querySelectorAll('[wire\\\\:id]');
        for (var i = 0; i < wireEls.length; i++) {
            var wireId = wireEls[i].getAttribute('wire\\\\:id');
            if (wireId) {
                var component = Livewire.find(wireId);
                if (component && component.$wire) {
                    component.$wire.call('extend');
                    return 'v3_call_extend:wireId=' + wireId;
                }
            }
        }
        if (window.Livewire) {
            var allComponents = Livewire.getByName || Livewire.components;
            if (allComponents) {
                for (var key in allComponents) {
                    var c = allComponents[key];
                    if (c && c.$wire) {
                        try { c.$wire.call('extend'); return 'v3_call_extend:name=' + key; } catch(e) {}
                    }
                }
            }
        }
        var wireEls2 = document.querySelectorAll('[wire\\\\:id]');
        for (var j = 0; j < wireEls2.length; j++) {
            var wid = wireEls2[j].getAttribute('wire\\\\:id');
            if (wid) {
                try {
                    var comp = window.livewire || window.Livewire;
                    var inst = comp.find(wid) || comp.componentsById[wid];
                    if (inst) { inst.$wire.extend(); return 'v3_direct_extend:wireId=' + wid; }
                } catch(e) {
                    try { inst.$wire.call('extend'); return 'v3_call_extend2:wireId=' + wid; } catch(e2) {}
                }
            }
        }
        return 'fail';
    } catch(e) { return 'error:' + e.message; }
})();
"""

_LW_V2_JS = """
(function(){
    try {
        if (window.livewire) { window.livewire.emit('extend'); return 'v2_emit_extend'; }
        if (window.Livewire && window.Livewire.emit) { window.Livewire.emit('extend'); return 'v2_emit_extend_alt'; }
        var allEls = document.querySelectorAll('*');
        for (var i = 0; i < allEls.length; i++) {
            var el = allEls[i];
            var t = (el.innerText || el.textContent || '').trim();
            if (t.indexOf('90') !== -1 && t.indexOf('min') !== -1) {
                var wc = el.getAttribute('wire\\\\:click') || el.getAttribute('wire\\\\:click\\\\.prevent');
                if (wc) {
                    var methodName = wc.replace(/[()]/g, '').replace(/\\$wire\\./g, '').trim();
                    if (window.livewire) { window.livewire.emit(methodName); return 'v2_emit_' + methodName; }
                }
            }
        }
        return 'fail';
    } catch(e) { return 'error:' + e.message; }
})();
"""

_LW_CLICK_JS = """
(function(){
    try {
        var allEls = document.querySelectorAll('*');
        var target = null, wireAttr = null;
        for (var i = 0; i < allEls.length; i++) {
            var el = allEls[i];
            var t = (el.innerText || el.textContent || '').trim();
            if (t.indexOf('90') !== -1 && t.indexOf('min') !== -1) {
                var rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && !el.disabled) {
                    var attrs = ['wire\\\\:click', 'wire\\\\:click\\\\.prevent', 'wire\\\\:click\\\\.stop'];
                    for (var a = 0; a < attrs.length; a++) {
                        var attr = el.getAttribute(attrs[a]);
                        if (attr) { wireAttr = attrs[a] + '=' + attr; target = el; break; }
                    }
                    if (!target) {
                        var parent = el.closest('[wire\\\\:click]') || el.closest('[wire\\\\:click\\\\.prevent]');
                        if (parent) {
                            var wc = parent.getAttribute('wire\\\\:click') || parent.getAttribute('wire\\\\:click\\\\.prevent');
                            wireAttr = 'parent_wire:click=' + wc; target = parent;
                        }
                    }
                    if (!target) { target = el; wireAttr = 'no_wire_attr'; }
                    break;
                }
            }
        }
        if (!target) return 'not_found';
        target.scrollIntoView({block: 'center', behavior: 'instant'});
        var rect = target.getBoundingClientRect();
        var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
        var opts = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, button: 0 };
        target.dispatchEvent(new MouseEvent('mousedown', opts));
        target.dispatchEvent(new MouseEvent('mouseup', opts));
        target.dispatchEvent(new MouseEvent('click', opts));
        var children = target.querySelectorAll('*');
        for (var k = 0; k < children.length && k < 5; k++) {
            children[k].dispatchEvent(new MouseEvent('mousedown', opts));
            children[k].dispatchEvent(new MouseEvent('mouseup', opts));
            children[k].dispatchEvent(new MouseEvent('click', opts));
        }
        return 'clicked:' + target.tagName + ':wire=' + wireAttr;
    } catch(e) { return 'error:' + e.message; }
})();
"""

_LW_DIAGNOSE_JS = """
(function(){
    var info = {};
    info.livewire_v3 = typeof Livewire !== 'undefined';
    info.livewire_v2 = typeof livewire !== 'undefined';
    var wireEls = document.querySelectorAll('[wire\\\\:id]');
    info.wire_elements = wireEls.length;
    info.wire_ids = [];
    for (var i = 0; i < wireEls.length; i++) {
        info.wire_ids.push({
            id: wireEls[i].getAttribute('wire\\\\:id'),
            tag: wireEls[i].tagName,
            class: (wireEls[i].className || '').substring(0, 80),
            wireClick: wireEls[i].getAttribute('wire\\\\:click') || wireEls[i].getAttribute('wire\\\\:click\\\\.prevent') || 'none'
        });
    }
    var btns = [];
    var allEls = document.querySelectorAll('button, a, [role=button]');
    for (var j = 0; j < allEls.length; j++) {
        var el = allEls[j];
        var t = (el.innerText || el.textContent || '').trim();
        if (t.indexOf('90') !== -1 && t.indexOf('min') !== -1) {
            btns.push({
                tag: el.tagName, text: t.substring(0, 40), disabled: el.disabled,
                wireClick: el.getAttribute('wire\\\\:click') || el.getAttribute('wire\\\\:click\\\\.prevent') || 'none',
                onClick: el.getAttribute('onclick') || 'none',
                class: (el.className || '').substring(0, 80),
                html: el.outerHTML.substring(0, 200)
            });
        }
    }
    info.renew_buttons = btns;
    if (window.Livewire) {
        info.livewire_version = Livewire.version || 'unknown';
        try { info.component_count = Object.keys(Livewire.componentsById || {}).length; } catch(e) {}
    }
    return JSON.stringify(info, null, 2);
})();
"""

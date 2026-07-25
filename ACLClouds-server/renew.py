#!/usr/bin/env python3
"""
ACLClouds (dash.aclclouds.com) 自动续期脚本
- 通过 Cookie 注入调用 Pelican 风格 API
- 免费服务到期前 2 天可续期 / Minecraft 免费服务到期前 2 小时可续期
- 默认阈值 48h, 命中则调用 POST /api/client/servers/{id}/upgrade/renew
- 不需要浏览器 / 不需要代理 / 不触发 Turnstile
"""

import os
import sys
import json
import time
import urllib.parse
from datetime import datetime, timezone

import requests

# ==================== 配置 ====================
BASE_URL = "https://dash.aclclouds.com"
RENEW_THRESHOLD_HOURS = int(os.environ.get("RENEW_THRESHOLD_HOURS", "48"))

# Cookie: 完整的浏览器 Cookie 字符串, 必须包含 XSRF-TOKEN 和 aclclouds_session
COOKIE = os.environ.get("ACL_COOKIES", "").strip()

# TG 通知
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()

# 多账号支持 (可选), 格式: name1|||cookie1\nname2|||cookie2
MULTI_ACCOUNTS = os.environ.get("ACL_ACCOUNTS", "").strip()

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


# ==================== 工具函数 ====================
def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg):
    print(msg, flush=True)


def send_tg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=15,
        )
    except Exception as e:
        log(f"⚠️ TG 推送失败: {e}")


def fmt_remaining(seconds):
    if seconds is None:
        return "?"
    if seconds < 0:
        return "已过期"
    seconds = int(seconds)
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    m = (seconds % 3600) // 60
    if d > 0:
        return f"{d}d {h}h {m}m"
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def build_session(cookie_str):
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/projects",
    })
    # 解析 cookie 字符串 - 处理 __Host- 和 __Secure- 前缀
    # 关键: 同时设置到 dash.aclclouds.com / aclclouds.com / .aclclouds.com
    # 因为 dash.aclclouds.com/api/client 会 302 重定向到 aclclouds.com/api/client
    # Cookie 必须在两个域上都存在, 否则重定向后丢失 → 401
    cookie_domains = ["dash.aclclouds.com", "aclclouds.com", ".aclclouds.com"]
    n_set = 0
    for kv in cookie_str.split(";"):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        k = k.strip()
        v = v.strip()
        # 移除 __Host- 和 __Secure- 前缀（requests 不支持这些前缀）
        clean_k = k
        if k.startswith("__Host-"):
            clean_k = k[7:]  # 移除 "__Host-" (7个字符)
        elif k.startswith("__Secure-"):
            clean_k = k[9:]  # 移除 "__Secure-" (9个字符)

        # 关键修复: 在多个域上设置 cookie, 保证重定向后仍能携带
        for d in cookie_domains:
            s.cookies.set(clean_k, v, domain=d, path="/")
        n_set += 1

    # 调试: 打印设置的 Cookie (去重显示)
    log(f"🔍 共解析 {n_set} 个 Cookie, 设置到 {len(cookie_domains)} 个域:")
    seen = set()
    for cookie in s.cookies:
        if cookie.name in seen:
            continue
        seen.add(cookie.name)
        log(f"   - {cookie.name} = {cookie.value[:30]}...")

    return s


def get_xsrf(session):
    """从 cookie 中提取并解码 XSRF-TOKEN (跨域查找)"""
    # 依次尝试几个可能的域
    for domain in ("dash.aclclouds.com", "aclclouds.com", ".aclclouds.com"):
        token = session.cookies.get("XSRF-TOKEN", domain=domain)
        if token:
            return urllib.parse.unquote(token)
    # 兜底: 不指定 domain
    token = session.cookies.get("XSRF-TOKEN")
    return urllib.parse.unquote(token) if token else None


def api_get(session, path):
    r = session.get(f"{BASE_URL}{path}", timeout=30, allow_redirects=True)
    return r


def api_post(session, path, payload=None):
    headers = {}
    token = get_xsrf(session)
    if token:
        headers["X-XSRF-TOKEN"] = token
    return session.post(f"{BASE_URL}{path}", headers=headers, json=payload or {}, timeout=30, allow_redirects=True)


def list_servers(session):
    # ACLClouds: GET /api/client 列服务器
    # 注意: dash.aclclouds.com/api/client 会 302 → aclclouds.com/api/client
    # Cookie 必须跨域, 已在 build_session 中设置
    r = api_get(session, "/api/client")
    if r.status_code != 200:
        # 打印重定向链路 + 最终 URL 方便排查
        log(f"🐛 GET /api/client HTTP {r.status_code}")
        log(f"   最终 URL: {r.url}")
        if r.history:
            log(f"   重定向链路:")
            for h in r.history:
                log(f"     {h.status_code} {h.url}")
        log(f"   响应: {r.text[:500]}")
        r.raise_for_status()
    j = r.json()
    if isinstance(j, dict):
        return j.get("data", [])
    return j if isinstance(j, list) else []


def server_detail(session, sid):
    r = api_get(session, f"/api/client/servers/{sid}")
    if r.status_code != 200:
        return None
    try:
        j = r.json()
        return j.get("attributes", j) if isinstance(j, dict) else j
    except Exception:
        return None


def find_expire(attrs, detail=None):
    """从多个可能字段找到期时间"""
    candidates = []
    if attrs:
        candidates.append(attrs)
    if detail:
        candidates.append(detail)
    # 也查 relationships
    for c in list(candidates):
        rel = c.get("relationships") if isinstance(c, dict) else None
        if rel:
            candidates.append(rel)
    for c in candidates:
        if not isinstance(c, dict):
            continue
        for key in ("expires_at", "expire_at", "renew_at", "renewable_at",
                    "expiration_date", "expires", "expiry"):
            v = c.get(key)
            if v:
                return key, v
    return None, None


def renew_server(session, sid):
    """调用续期 API, 返回 (response, captcha_required_bool)"""
    r = api_post(session, f"/api/client/servers/{sid}/upgrade/renew")
    captcha_required = False
    if r.status_code == 403:
        try:
            j = r.json()
            if isinstance(j, dict) and j.get("code") == "captcha_required":
                captcha_required = True
        except Exception:
            pass
    return r, captcha_required


# ==================== 单账号续期流程 ====================
def process_account(label, cookie_str):
    log(f"\n{'='*60}")
    log(f"👤 账号: {label}")
    log(f"{'='*60}")

    if not cookie_str:
        return {"label": label, "ok": False, "msg": "Cookie 为空"}

    session = build_session(cookie_str)

    # 1. 拉取服务器列表
    try:
        servers = list_servers(session)
    except Exception as e:
        # 失败时打印响应内容方便排查
        try:
            r = api_get(session, "/api/client")
            log(f"🐛 /api/client 响应 HTTP {r.status_code}: {r.text[:500]}")
        except Exception as e2:
            log(f"🐛 二次请求失败: {e2}")
        return {"label": label, "ok": False, "msg": f"获取服务器列表失败: {e}"}

    if not servers:
        # 列表为空时打印原始响应帮助排查
        try:
            r = api_get(session, "/api/client")
            log(f"🐛 /api/client 原始响应 HTTP {r.status_code}: {r.text[:500]}")
        except Exception:
            pass
        return {"label": label, "ok": True, "msg": "没有服务器", "results": []}

    log(f"📦 共 {len(servers)} 台服务器")
    # 调试: 打印第一台服务器的原始数据结构
    if servers:
        first = servers[0]
        if isinstance(first, dict):
            attrs = first.get("attributes", first)
            log(f"🐛 首个服务器字段: {list(attrs.keys()) if isinstance(attrs, dict) else type(attrs).__name__}")
    now = datetime.now(timezone.utc)
    results = []
    renewed = 0
    skipped = 0
    failed = 0

    for idx, srv in enumerate(servers, 1):
        attrs = srv.get("attributes", srv) if isinstance(srv, dict) else {}
        sid = attrs.get("identifier") or attrs.get("id") or attrs.get("uuid") or attrs.get("uuid_short") or attrs.get("server_id")
        name = attrs.get("name", f"server-{idx}")
        stype = attrs.get("egg") or attrs.get("egg_id") or attrs.get("server_type") or "unknown"

        if not sid:
            results.append(f"⚠️ {name}: 缺少 server id")
            continue

        log(f"\n[{idx}/{len(servers)}] 🖥️ {name} (id={sid}, type={stype})")

        # 取到期时间 - 列表可能没有, 调详情
        _, expire_str = find_expire(attrs)
        detail = None
        if not expire_str:
            detail = server_detail(session, sid)
            if detail:
                _, expire_str = find_expire(attrs, detail)

        if not expire_str:
            results.append(f"⚠️ {name}: 无到期时间字段")
            skipped += 1
            continue

        expire = parse_iso(expire_str)
        if not expire:
            results.append(f"⚠️ {name}: 到期时间格式错误 ({expire_str})")
            skipped += 1
            continue

        remaining = (expire - now).total_seconds()
        remaining_fmt = fmt_remaining(remaining)
        log(f"📅 到期: {expire_str}  剩余: {remaining_fmt}")

        if remaining > RENEW_THRESHOLD_HOURS * 3600:
            results.append(f"⏭️ {name}: 剩 {remaining_fmt}, 未到阈值 {RENEW_THRESHOLD_HOURS}h")
            skipped += 1
            continue

        # 续期
        try:
            r, captcha_required = renew_server(session, sid)
        except Exception as e:
            results.append(f"❌ {name}: 请求异常 {e}")
            failed += 1
            continue

        if r.status_code in (200, 201, 202, 204):
            # 重新查到期时间
            time.sleep(1.5)
            new_detail = server_detail(session, sid)
            _, new_expire_str = find_expire(attrs, new_detail)
            new_expire = parse_iso(new_expire_str) if new_expire_str else None
            if new_expire:
                new_remaining = (new_expire - now).total_seconds()
                new_fmt = fmt_remaining(new_remaining)
            else:
                new_fmt = "?"
            results.append(f"✅ {name}: {remaining_fmt} → {new_fmt}")
            renewed += 1
            log(f"✅ 续期成功: {remaining_fmt} → {new_fmt}")
        elif captcha_required:
            results.append(f"🛡️ {name}: 需要 Turnstile 验证 (纯 API 无法通过, 跳过)")
            failed += 1
            log(f"🛡️ {name}: 需要 Turnstile, 纯 API 无法通过")
        else:
            body = ""
            try:
                body = r.text[:300]
            except Exception:
                pass
            results.append(f"❌ {name}: HTTP {r.status_code} {body}")
            failed += 1
            log(f"❌ 续期失败: HTTP {r.status_code} {body}")

        time.sleep(2)  # 礼貌延时

    return {
        "label": label,
        "ok": True,
        "total": len(servers),
        "renewed": renewed,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


# ==================== 主入口 ====================
def collect_accounts():
    """返回 [(label, cookie_str), ...]"""
    accounts = []

    # 1. 多账号 (优先)
    if MULTI_ACCOUNTS:
        for line in MULTI_ACCOUNTS.splitlines():
            line = line.strip()
            if not line:
                continue
            if "|||" in line:
                name, ck = line.split("|||", 1)
                accounts.append((name.strip(), ck.strip()))
            else:
                accounts.append((f"account-{len(accounts)+1}", line))

    # 2. 单账号兜底
    if not accounts and COOKIE:
        accounts.append(("main", COOKIE))

    return accounts


def build_summary(all_results):
    renewed_total = sum(r.get("renewed", 0) for r in all_results)
    skipped_total = sum(r.get("skipped", 0) for r in all_results)
    failed_total = sum(r.get("failed", 0) for r in all_results)
    total_servers = sum(r.get("total", 0) for r in all_results)

    lines = ["🎮 *ACLClouds 自动续期*", f"⏰ {now_str()}", ""]
    lines.append(f"📊 总服务器: {total_servers} | ✅ {renewed_total} | ⏭️ {skipped_total} | ❌ {failed_total}")
    lines.append("")

    for r in all_results:
        if not r.get("ok"):
            lines.append(f"👤 {r['label']}: ❌ {r.get('msg', '失败')}")
            continue
        if not r.get("results"):
            lines.append(f"👤 {r['label']}: {r.get('msg', '无服务器')}")
            continue
        lines.append(f"👤 *{r['label']}* (✅{r.get('renewed',0)} ⏭️{r.get('skipped',0)} ❌{r.get('failed',0)})")
        for line in r["results"]:
            lines.append(f"  {line}")
        lines.append("")

    return "\n".join(lines)


def main():
    log(f"🚀 ACLClouds 续期脚本启动 @ {now_str()}")
    log(f"⚙️ 续期阈值: {RENEW_THRESHOLD_HOURS}h")

    accounts = collect_accounts()
    if not accounts:
        msg = "❌ 未配置 ACL_COOKIES 或 ACL_ACCOUNTS"
        log(msg)
        send_tg(msg)
        sys.exit(1)

    log(f"📋 共 {len(accounts)} 个账号待处理")

    all_results = []
    for label, ck in accounts:
        try:
            res = process_account(label, ck)
        except Exception as e:
            res = {"label": label, "ok": False, "msg": f"异常: {e}"}
        all_results.append(res)

    summary = build_summary(all_results)
    print("\n" + summary + "\n")
    send_tg(summary)


if __name__ == "__main__":
    main()

import urllib.request, urllib.parse
from datetime import datetime
from util import log
from cfg import *

def send_tg(msg, sn="", tt=""):
    """发送 Telegram 通知"""
    if not TG_BOT:
        log("TG 配置错误: TG_BOT_TOKEN 未设置", "ERR")
        return False

    if not TG_CHAT:
        log("TG 配置错误: TG_CHAT_ID 未设置", "ERR")
        return False

    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        safe_msg = msg[:2000]
        safe_tt = tt[:500] if tt else ""
        safe_sn = sn[:50] if sn else ""

        t = f"Gaming4Free Pro\n服务器: {safe_sn}\n时间: {now}\n状态: {safe_msg}\n剩余: {safe_tt}\n模式: Renew-Pro v33"

        u = f"https://api.telegram.org/bot{TG_BOT}/sendMessage"

        log(f"发送 TG 通知: {safe_msg[:50]}...", "INFO")

        data = f"chat_id={urllib.parse.quote(TG_CHAT)}&text={urllib.parse.quote(t)}".encode()
        req = urllib.request.Request(u, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        with urllib.request.urlopen(req, timeout=10) as response:
            result = response.read().decode('utf-8')
            import json
            res = json.loads(result)
            if res.get('ok'):
                log("TG 通知成功", "OK")
                return True
            else:
                log(f"TG 通知失败: {res.get('description', '未知错误')}", "ERR")
                return False

    except urllib.error.HTTPError as e:
        log(f"TG HTTP 错误: {e.code} - {e.reason}", "ERR")
        return False
    except urllib.error.URLError as e:
        log(f"TG 网络错误: {e.reason}", "ERR")
        return False
    except Exception as e:
        log(f"TG 通知异常: {type(e).__name__} - {e}", "ERR")
        import traceback
        log(traceback.format_exc(), "ERR")
        return False


def check_tg_config():
    """检查 TG 配置是否有效"""
    if not TG_BOT:
        log("TG 配置: TG_BOT_TOKEN 未设置", "WARN")
        return False

    if not TG_CHAT:
        log("TG 配置: TG_CHAT_ID 未设置", "WARN")
        return False

    # 正确格式校验
    if not TG_BOT.isdigit():
        log("TG 配置: TG_BOT_TOKEN 格式错误（应以数字开头）", "WARN")
        return False

    if not TG_CHAT.lstrip("-").isdigit():
        log("TG 配置: TG_CHAT_ID 格式错误（应为数字，可为负数）", "WARN")
        return False

    log(f"TG 配置检查通过: bot=TG_BOT... / chat={TG_CHAT}")
    return True

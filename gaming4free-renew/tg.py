import requests
import os

TG_TOKEN = os.getenv("TG_BOT_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")


def tg(msg: str, photo_path: str = None):
    """发送 Telegram 通知，支持带截图"""
    if not (TG_TOKEN and TG_CHAT_ID):
        print(f"[{__name__}] ⚠️ TG 未配置，跳过通知")
        return

    try:
        if photo_path and os.path.exists(photo_path):
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
            with open(photo_path, 'rb') as f:
                requests.post(url, data={'chat_id': TG_CHAT_ID, 'caption': msg},
                              files={'photo': f}, timeout=10)
        else:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
            requests.post(url, json={
                "chat_id": TG_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML",
            }, timeout=10)
        print(f"[{__name__}] ✅ TG 通知发送成功")
    except Exception as e:
        print(f"[{__name__}] ⚠️ TG 通知失败: {e}")

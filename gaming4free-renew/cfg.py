import os
import sys

RENEW_URL=os.environ.get("GAME4FREE_RENEW_URL","").strip()
COOKIE=os.environ.get("GAME4FREE_COOKIE","").strip()
ACCOUNTS=[]
for line in os.environ.get("GAME4FREE_ACCOUNTS","").split("\n"):
    line=line.strip()
    if not line: continue
    parts=line.split("|||")
    if len(parts)>=3: ACCOUNTS.append((parts[0].strip(),parts[1].strip(),parts[2].strip()))
for line in os.environ.get("GAME4FREE_ACCOUNT","").split("\n"):
    line=line.strip()
    if not line: continue
    parts=line.split("|||")
    if len(parts)>=3:
        if "@" in parts[2] and not parts[1].startswith("http"):
            ACCOUNTS.append((parts[0].strip(),"https://control.gaming4free.net/server/"+parts[1].strip(),parts[2].strip()))
TG_BOT=os.environ.get("TG_BOT_TOKEN","")
TG_CHAT=os.environ.get("TG_CHAT_ID","")
MAX_TRIES=3; THRESHOLD=45*3600; MAX_ROUNDS=10

# 验证 TG 配置
if TG_BOT and TG_CHAT:
    if not TG_BOT.startswith(""):
        print("⚠️  警告: TG_BOT_TOKEN 格式错误（应以数字开头）", file=sys.stderr)
    if not TG_CHAT.startswith(""):
        print("⚠️  警告: TG_CHAT_ID 格式错误（应以数字开头）", file=sys.stderr)

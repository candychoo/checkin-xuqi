#!/bin/bash
# Gaming4Free Pro 续期脚本 - Telegram 通知测试脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENEW_SCRIPT="${SCRIPT_DIR}/renew.py"

echo "=========================================="
echo "Gaming4Free Pro - Telegram 通知测试"
echo "=========================================="
echo ""

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误：未找到 python3"
    exit 1
fi

echo "✅ Python 版本：$(python3 --version)"
echo ""

# 检查配置
echo "🔍 检查 Telegram 配置..."
cd "$SCRIPT_DIR"
python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from cfg import TG_BOT, TG_CHAT

if not TG_BOT:
    print('❌ TG_BOT_TOKEN 未设置')
    print('')
    print('请在运行此脚本前设置环境变量：')
    print('  export TG_BOT_TOKEN=\"你的Bot_Token\"')
    print('  export TG_CHAT_ID=\"你的Chat_ID\"')
    sys.exit(1)

if not TG_CHAT:
    print('❌ TG_CHAT_ID 未设置')
    print('')
    print('请在运行此脚本前设置环境变量：')
    print('  export TG_BOT_TOKEN=\"你的Bot_Token\"')
    print('  export TG_CHAT_ID=\"你的Chat_ID\"')
    sys.exit(1)

print('✅ TG 配置检查通过')
print(f'   TG_BOT_TOKEN: {TG_BOT[:10]}...')
print(f'   TG_CHAT_ID: {TG_CHAT[:10]}...')
" || exit 1

echo ""
echo "=========================================="
echo "📤 发送 TG 测试通知..."
echo "=========================================="
echo ""

# 运行测试
cd "$SCRIPT_DIR"
python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from tg import check_tg_config, send_tg

print('🔍 检查配置...')
tg_config_ok = check_tg_config()

if tg_config_ok:
    print('📤 发送测试通知...')
    try:
        result = send_tg('🧪 TG 通知测试', 'Gaming4Free Pro', '配置检查通过')
        if result:
            print('')
            print('✅ 测试通知发送成功！')
            print('')
            print('请检查你的 Telegram 账号是否收到通知')
        else:
            print('')
            print('❌ 测试通知发送失败')
            print('请检查配置和日志')
    except Exception as e:
        print('')
        print(f'❌ 测试通知异常: {e}')
        import traceback
        traceback.print_exc()
else:
    print('❌ TG 配置错误')
    print('请检查配置文件和日志')
" || exit 1

echo ""
echo "=========================================="
echo "✅ 测试完成"
echo "=========================================="
echo ""

# 显示环境变量
echo "当前环境变量："
echo ""
echo "TG_BOT_TOKEN: ${TG_BOT_TOKEN:+***已设置***}"
echo "TG_CHAT_ID: ${TG_CHAT_ID:+***已设置***}"
echo ""

# 显示配置文件位置
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "配置文件: $SCRIPT_DIR/.env"
    echo ""
    echo "如需修改配置，编辑："
    echo "  nano $SCRIPT_DIR/.env"
    echo "  或："
    echo "  vi $SCRIPT_DIR/.env"
    echo ""
fi

# 显示详细文档
echo "详细说明请查看："
echo "  TG_SETUP.md"
echo ""

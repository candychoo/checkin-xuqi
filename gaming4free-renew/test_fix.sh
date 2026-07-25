#!/bin/bash
# Gaming4Free Pro 续期脚本测试脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENEW_SCRIPT="${SCRIPT_DIR}/renew.py"
TEST_MODE="${1:-false}"

echo "=========================================="
echo "Gaming4Free Pro 续期脚本测试"
echo "=========================================="
echo ""

# 检查 Python 和依赖
echo "🔍 检查环境..."
if ! command -v python3 &> /dev/null; then
    echo "❌ 错误：未找到 python3"
    exit 1
fi

echo "✅ Python 版本：$(python3 --version)"
echo ""

# 检查脚本是否存在
if [ ! -f "${RENEW_SCRIPT}" ]; then
    echo "❌ 错误：未找到 renew.py 脚本"
    exit 1
fi

# 检查依赖
echo "🔍 检查依赖..."
python3 -c "import seleniumbase" 2>/dev/null || echo "⚠️  警告：seleniumbase 未安装"
python3 -c "import selenium" 2>/dev/null || echo "⚠️  警告：selenium 未安装"
python3 -c "import requests" 2>/dev/null || echo "⚠️  警告：requests 未安装"
echo ""

# 检查配置
echo "🔍 检查配置..."
if [ -f "${SCRIPT_DIR}/cfg.py" ]; then
    echo "✅ 找到配置文件：cfg.py"
    echo ""
    echo "账号配置："
    python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}')
from cfg import ACCOUNTS
if ACCOUNTS:
    for i, (name, url, cookie) in enumerate(ACCOUNTS, 1):
        print(f'  账号 {i}: {name}')
else:
    print('  未配置任何账号')
"
else
    echo "⚠️  警告：未找到配置文件 cfg.py"
fi
echo ""

# 检查脚本语法
echo "🔍 检查脚本语法..."
if python3 -m py_compile "${RENEW_SCRIPT}" 2>/dev/null; then
    echo "✅ 脚本语法检查通过"
else
    echo "❌ 脚本语法错误"
    exit 1
fi
echo ""

# 显示版本信息
echo "🔍 脚本版本信息..."
grep -E "^__version__|^#.*v\d+\.\d+" "${RENEW_SCRIPT}" | head -5
echo ""

# 测试模式（仅检查配置和语法）
if [ "${TEST_MODE}" = "true" ]; then
    echo "✅ 测试完成（测试模式）"
    echo ""
    echo "如需运行实际测试，请使用以下命令："
    echo "  xvfb-run --auto-servernum --server-args='-screen 0 1920x1080x24' python3 ${RENEW_SCRIPT}"
    exit 0
fi

# 显示运行命令
echo "=========================================="
echo "✅ 所有检查通过！"
echo "=========================================="
echo ""
echo "运行命令："
echo "  xvfb-run --auto-servernum --server-args='-screen 0 1920x1080x24' python3 ${RENEW_SCRIPT}"
echo ""
echo "调试模式（显示浏览器界面）："
echo "  xvfb-run --auto-servernum --server-args='-screen 0 1920x1080x24' python3 ${RENEW_SCRIPT}"
echo ""
echo "查看日志："
echo "  tail -f ${SCRIPT_DIR}/debug_output/*.log 2>/dev/null"
echo ""

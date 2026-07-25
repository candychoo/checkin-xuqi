#!/bin/bash
# Gaming4Free Pro 续期脚本修复应用脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RENEW_SCRIPT="${SCRIPT_DIR}/renew.py"
BACKUP_SCRIPT="${RENEW_SCRIPT}.bak"
FIXED_SCRIPT="${SCRIPT_DIR}/renew_fixed.py"

echo "=========================================="
echo "Gaming4Free Pro 续期脚本修复应用"
echo "=========================================="
echo ""

# 检查脚本是否存在
if [ ! -f "${RENEW_SCRIPT}" ]; then
    echo "❌ 错误：未找到 renew.py 脚本"
    exit 1
fi

if [ ! -f "${FIXED_SCRIPT}" ]; then
    echo "❌ 错误：未找到 renew_fixed.py 脚本"
    exit 1
fi

# 备份原脚本
if [ -f "${RENEW_SCRIPT}" ]; then
    if [ -f "${BACKUP_SCRIPT}" ]; then
        echo "⚠️  警告：备份文件已存在，跳过备份"
    else
        echo "📦 备份原脚本..."
        cp "${RENEW_SCRIPT}" "${BACKUP_SCRIPT}"
        echo "✅ 备份完成：${BACKUP_SCRIPT}"
    fi
else
    echo "❌ 错误：原脚本不存在"
    exit 1
fi

# 应用修复
echo "🔧 应用修复..."
cp "${FIXED_SCRIPT}" "${RENEW_SCRIPT}"
echo "✅ 修复完成：${RENEW_SCRIPT}"

# 检查是否安装了 jq（用于显示版本信息）
if command -v jq &> /dev/null; then
    VERSION=$(grep -oP 'v\d+\.\d+' "${RENEW_SCRIPT}" | head -1)
    if [ -n "${VERSION}" ]; then
        echo ""
        echo "🎉 当前版本：${VERSION}"
    fi
fi

echo ""
echo "=========================================="
echo "修复完成！"
echo "=========================================="
echo ""
echo "使用方法："
echo "  python3 ${RENEW_SCRIPT}"
echo ""
echo "如需回滚："
echo "  mv ${BACKUP_SCRIPT} ${RENEW_SCRIPT}"
echo ""

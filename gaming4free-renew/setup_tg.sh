#!/bin/bash
# Gaming4Free Pro 续期脚本 - Telegram 通知快速配置脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================="
echo "Gaming4Free Pro - Telegram 通知配置"
echo "=========================================="
echo ""

# 检查是否在 Git Bash / MSYS2 / WSL
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo "检测到 Windows 环境（Git Bash / MSYS2 / WSL）"
    echo ""

    # 检查 PowerShell
    if command -v pwsh &> /dev/null; then
        echo "✅ 找到 PowerShell"
        echo ""
        echo "请在 PowerShell 中运行以下命令："
        echo ""
        echo "  # 1. 创建配置文件"
        echo "  New-Item -ItemType File -Path '$SCRIPT_DIR/.env' -Force | Out-Null"
        echo ""
        echo "  # 2. 编辑配置文件"
        echo "  notepad '$SCRIPT_DIR/.env'"
        echo ""
        echo "  # 3. 在文件中添加以下内容："
        echo "  export TG_BOT_TOKEN='你的Bot_Token'"
        echo "  export TG_CHAT_ID='你的Chat_ID'"
        echo ""
        echo "  # 4. 加载配置"
        echo "  source '$SCRIPT_DIR/.env'"
        echo ""
        echo "  # 5. 验证配置"
        echo "  echo \$TG_BOT_TOKEN"
        echo "  echo \$TG_CHAT_ID"
        echo ""
        exit 0
    fi

    # 检查 CMD
    if command -v cmd.exe &> /dev/null; then
        echo "✅ 找到 CMD"
        echo ""
        echo "请在 CMD 中运行以下命令："
        echo ""
        echo "  # 1. 创建配置文件"
        echo "  echo set TG_BOT_TOKEN=你的Bot_Token > $SCRIPT_DIR\\env.bat"
        echo "  echo set TG_CHAT_ID=你的Chat_ID >> $SCRIPT_DIR\\env.bat"
        echo ""
        echo "  # 2. 编辑配置文件"
        echo "  notepad $SCRIPT_DIR\\env.bat"
        echo ""
        echo "  # 3. 加载配置"
        echo "  call $SCRIPT_DIR\\env.bat"
        echo ""
        echo "  # 4. 验证配置"
        echo "  echo %TG_BOT_TOKEN%"
        echo "  echo %TG_CHAT_ID%"
        echo ""
        exit 0
    fi
fi

# Linux / macOS
echo "✅ 检测到 Linux / macOS 环境"
echo ""

# 检查 .zshrc 或 .bashrc
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
    SHELL_NAME="zsh"
elif [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
    SHELL_NAME="bash"
else
    SHELL_RC="$HOME/.bash_profile"
    SHELL_NAME="bash"
fi

echo "✅ 找到配置文件: $SHELL_RC ($SHELL_NAME)"
echo ""

# 创建配置文件
CONFIG_FILE="$SCRIPT_DIR/.env"
if [ -f "$CONFIG_FILE" ]; then
    echo "⚠️  警告: 配置文件已存在 ($CONFIG_FILE)"
    read -p "是否覆盖？(y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "取消配置"
        exit 0
    fi
fi

echo "📝 创建配置文件: $CONFIG_FILE"
cat > "$CONFIG_FILE" << 'EOF'
# Gaming4Free Pro 续期配置
# 请将下面的占位符替换为实际的值

# Bot Token (从 @BotFather 获取)
# 格式：123456789:ABCdefGHIjklMNOpqrsTUVwxyz
# TG_BOT_TOKEN=""

# Chat ID (从 @userinfobot 获取)
# 格式：123456789
# TG_CHAT_ID=""
EOF

echo ""
echo "✅ 配置文件已创建"
echo ""
echo "下一步："
echo "  1. 编辑配置文件："
echo "     nano $CONFIG_FILE"
echo "     或："
echo "     vi $CONFIG_FILE"
echo ""
echo "  2. 替换占位符："
echo "     TG_BOT_TOKEN=\"你的Bot_Token\""
echo "     TG_CHAT_ID=\"你的Chat_ID\""
echo ""
echo "  3. 加载配置："
echo "     source $CONFIG_FILE"
echo ""
echo "  4. 验证配置："
echo "     echo \$TG_BOT_TOKEN"
echo "     echo \$TG_CHAT_ID"
echo ""
echo "  5. 运行脚本测试："
echo "     python3 renew.py"
echo ""

# 询问是否立即编辑
read -p "是否立即编辑配置文件？(Y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]] || [ -z "$REPLY" ]; then
    if command -v nano &> /dev/null; then
        nano "$CONFIG_FILE"
    elif command -v vi &> /dev/null; then
        vi "$CONFIG_FILE"
    else
        echo "⚠️  未找到编辑器，请手动编辑配置文件"
        echo "   文件路径: $CONFIG_FILE"
    fi
fi

echo ""
echo "=========================================="
echo "✅ 配置完成！"
echo "=========================================="
echo ""
echo "如需设置开机自动加载配置："
echo ""
echo "  # 添加到 $SHELL_RC"
echo "  echo 'source $SCRIPT_DIR/.env' >> $SHELL_RC"
echo ""
echo "或"
echo ""
echo "  # 创建符号链接"
echo "  ln -sf $CONFIG_FILE $HOME/.gaming4free.env"
echo ""
echo "详细说明请查看："
echo "  TG_SETUP.md"
echo ""

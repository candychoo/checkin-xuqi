# Telegram 通知配置指南

## 📋 配置说明

Gaming4Free Pro 续期脚本支持 Telegram 通知，可在续期成功或失败时发送消息到你的 Telegram 账号。

---

## 🔧 配置步骤

### 1. 创建 Telegram Bot

1. 打开 Telegram，搜索 **@BotFather**
2. 发送命令：`/newbot`
3. 按提示设置 Bot 名称和用户名
4. BotFather 会返回一个 **Bot Token**（格式类似：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

**重要**：请妥善保管 Bot Token，不要泄露给他人。

---

### 2. 获取 Chat ID

有两种方式获取你的 Chat ID：

#### 方法 1：使用 @userinfobot（推荐）

1. 在 Telegram 中搜索 **@userinfobot**
2. 发送任意消息给 Bot
3. Bot 会返回你的 **Chat ID**（格式类似：`123456789`）

#### 方法 2：使用 API

```bash
curl https://api.telegram.org/bot<TG_BOT_TOKEN>/getUpdates
```

返回结果中的 `result` 数组中，第一条消息的 `message.chat.id` 就是你的 Chat ID。

---

### 3. 配置环境变量

在运行脚本前，设置以下环境变量：

#### Linux / macOS / Git Bash
```bash
export TG_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
export TG_CHAT_ID="123456789"
```

#### Windows (PowerShell)
```powershell
$env:TG_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
$env:TG_CHAT_ID = "123456789"
```

#### Windows (CMD)
```cmd
set TG_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
set TG_CHAT_ID=123456789
```

#### Docker / Kubernetes
```yaml
environment:
  - TG_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
  - TG_CHAT_ID=123456789
```

#### Docker Compose
```yaml
services:
  renew:
    environment:
      - TG_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
      - TG_CHAT_ID=123456789
```

---

## 🚀 测试通知

配置完成后，运行以下命令测试通知：

```bash
cd /path/to/gaming4free-renew
python3 renew.py
```

查看日志输出，应该看到：

```
[2026-07-25 07:31:02] 🔍 检查 Telegram 通知配置...
[2026-07-25 07:31:02] ✅ TG 配置: 123456789:ABCdef... / 123456789...
[2026-07-25 07:31:02] 📤 发送 TG 测试通知...
[2026-07-25 07:31:03] ✅ TG 测试通知发送成功
```

如果看到 `❌ TG 配置错误` 或 `❌ TG 通知发送失败`，请检查配置。

---

## 📊 通知示例

### 续期成功通知
```
Gaming4Free Pro
服务器: [gaming4] 05:50:10
时间: 2026-07-25 07:50:10
状态: 🎉 续期成功
剩余: +90 分钟
模式: Renew-Pro v33
```

### 续期失败通知
```
Gaming4Free Pro
服务器: [gaming4] 05:50:10
时间: 2026-07-25 07:50:10
状态: ❌ 续期失败
剩余: 增量: -189s
模式: Renew-Pro v33
```

---

## ⚙️ 故障排查

### 问题 1：TG 配置错误 - TG_BOT_TOKEN 未设置

**解决方案**：
```bash
# 检查环境变量
echo $TG_BOT_TOKEN

# 如果为空，设置环境变量
export TG_BOT_TOKEN="你的Bot_Token"
export TG_CHAT_ID="你的Chat_ID"
```

---

### 问题 2：TG 配置错误 - TG_CHAT_ID 未设置

**解决方案**：
```bash
# 检查环境变量
echo $TG_CHAT_ID

# 如果为空，设置环境变量
export TG_CHAT_ID="你的Chat_ID"
```

---

### 问题 3：TG 通知发送失败

**可能原因**：

1. **Bot Token 错误**
   - 检查 Bot Token 是否正确
   - 重新从 BotFather 获取 Token

2. **Chat ID 错误**
   - 检查 Chat ID 是否正确
   - 使用 @userinfobot 确认 Chat ID

3. **网络问题**
   - 检查网络连接
   - 确保可以访问 `api.telegram.org`

4. **Bot 被封禁**
   - 确保没有向 Bot 发送违规内容

**解决方案**：
```bash
# 检查网络连接
curl https://api.telegram.org

# 查看详细错误日志
python3 renew.py 2>&1 | grep -A 5 "TG"
```

---

### 问题 4：通知发送成功但没收到消息

**可能原因**：

1. **Bot 未添加到聊天**
   - 确保你已将 Bot 添加到你的 Telegram 聊天

2. **隐私设置**
   - 检查 Bot 的隐私设置（`/mybots` → 设置 → 隐私模式）
   - 如果启用隐私模式，需要先向 Bot 发送消息

3. **消息被过滤**
   - 检查 Bot 是否被设置为"不通知"

**解决方案**：
1. 向 Bot 发送任意消息：`/start`
2. 检查 Bot 设置中的隐私模式
3. 确保没有屏蔽 Bot 的消息

---

## 📝 配置文件示例

### Linux / macOS（.bashrc 或 .zshrc）
```bash
# Gaming4Free Pro 续期配置
export GAME4FREE_RENEW_URL="https://control.gaming4free.net/server/kuya.g4f.gg"
export GAME4FREE_COOKIE="session=xxx; token=yyy"
export TG_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
export TG_CHAT_ID="123456789"
```

### Windows（PowerShell profile）
```powershell
# Gaming4Free Pro 续期配置
$env:GAME4FREE_RENEW_URL = "https://control.gaming4free.net/server/kuya.g4f.gg"
$env:GAME4FREE_COOKIE = "session=xxx; token=yyy"
$env:TG_BOT_TOKEN = "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
$env:TG_CHAT_ID = "123456789"
```

---

## 🔒 安全建议

1. **不要将 Token 提交到 Git**
   - 将 `.env` 文件添加到 `.gitignore`
   - 使用环境变量或密钥管理工具

2. **定期更换 Token**
   - 如果怀疑 Token 泄露，重新从 BotFather 获取

3. **限制 Bot 权限**
   - 只授予 Bot 必要的权限
   - 不要将 Bot 添加到敏感群组

4. **使用强密码**
   - 如果 Bot 有密码保护，设置强密码

---

## 📞 获取帮助

如果遇到问题：

1. 查看日志输出：`python3 renew.py 2>&1 | tee log.txt`
2. 检查网络连接：`curl https://api.telegram.org`
3. 确认配置格式：`echo $TG_BOT_TOKEN && echo $TG_CHAT_ID`
4. 参考本文档的故障排查部分

---

## 📄 相关文档

- [Telegram Bot API](https://core.telegram.org/bots/api)
- [BotFather 指南](https://core.telegram.org/bots#botfather)
- [Gaming4Free Pro 续期脚本](./renew.py)

---

**版本**：v33
**更新日期**：2026-07-25
**状态**：✅ 已修复

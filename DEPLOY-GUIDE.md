# ACLClouds 自动续期部署说明

## 方案一：Cookie 方式（推荐，稳定）

### 配置步骤

1. **获取 Cookie**
   - 登录 https://dash.aclclouds.com
   - 按 F12 → Console 标签
   - 运行 `copy(document.cookie)` 复制完整 Cookie

2. **添加 GitHub Secrets**
   - 进入仓库 → Settings → Secrets and variables → Actions
   - 添加以下 Secret：

   | Secret 名称 | 说明 | 示例 |
   |-------------|------|------|
   | `ACL_COOKIES` | 浏览器完整 Cookie（必须包含 XSRF-TOKEN 和 aclclouds_session） | `XSRF-TOKEN=xxx; aclclouds_session=yyy; ...` |
   | `RENEW_THRESHOLD_HOURS` | 续期阈值（小时），默认 48 | `48` |
   | `TG_BOT_TOKEN` | Telegram Bot Token（可选，用于通知） | `123456:ABC-DEF...` |
   | `TG_CHAT_ID` | Telegram Chat ID（可选） | `123456789` |

3. **触发 Workflow**
   - 进入 Actions → `ACLClouds-卡卡续期` → Run workflow

---

## 方案二：Google OAuth 自动登录

### 配置步骤

1. **添加 GitHub Secrets**
   - 进入仓库 → Settings → Secrets and variables → Actions
   - 添加以下 Secret：

   | Secret 名称 | 说明 | 示例 |
   |-------------|------|------|
   | `ACL_GOOGLE_EMAIL` | 你的 Google 邮箱 | `user@gmail.com` |
   | `ACL_GOOGLE_PASSWORD` | 你的 Google 密码 | `your-password` |
   | `RENEW_THRESHOLD_HOURS` | 续期阈值（小时），默认 48 | `48` |
   | `TG_BOT_TOKEN` | Telegram Bot Token（可选） | `123456:ABC-DEF...` |
   | `TG_CHAT_ID` | Telegram Chat ID（可选） | `123456789` |

2. **触发 Workflow**
   - 进入 Actions → `ACLClouds-Google自动续期` → Run workflow

### ⚠️ 注意事项
- Google 登录可能遇到二次验证或设备确认
- 建议在 Google 账号设置中开启"不够安全的应用"访问
- 或使用 Google 应用专用密码

---

## 常见问题

### Q: Cookie 返回 401 Unauthorized？
A: 确保 Cookie 包含 `aclclouds_session` 字段。如果只有 `acl_consent` 和 `XSRF-TOKEN`，请重新登录并复制完整 Cookie。

### Q: 续期失败？
A: 检查服务器是否已到期前 2 天。ACLClouds 限制到期前 2 天才允许续期。

### Q: 如何立即测试续期？
A: 在 Settings → Variables 中将 `RENEW_THRESHOLD_HOURS` 改为 `1`，然后手动触发 Workflow。

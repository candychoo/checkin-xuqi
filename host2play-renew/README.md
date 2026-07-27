# Host2Play 自动续期（SeleniumBase UC + Hysteria2 代理版）https://panel.host2play.net/dashboard

> 利用 GitHub Actions + SeleniumBase UC 模式 + Hysteria2 住宅代理自动续期 Host2Play 服务器。脚本支持 CF Turnstile 自动验证。

## 🎯 原理

- **SeleniumBase UC 模式** — 更强的反检测能力（UC = Undetected Chrome）
- **Hysteria2/Sing-box 代理** — 住宅IP节点，规避地区限制
- **CF Turnstile CDP** — 自动完成 Cloudflare 挑战验证
- **Cookie 注入** — 跳过登录，直接访问续期页面
- **统一 TG 通知** — 续期结果Telegram推送

## 📁 文件结构

```
host2play-renew/
├── main.py              # 续期主脚本
├── requirements.txt     # Python 依赖
└── output/screenshots/  # 截图输出目录
```

## 🚀 部署步骤

### 1. 配置 Secrets

进入仓库 → `Settings` → `Secrets and variables` → `Actions` → `New repository secret`

支持 **单账号** 或 **多账号** 两种配置方式：

#### 方式 A：单账号（简单）

| Secret 名 | 必填 | 说明 |
|---|---|---|
| `H2P_RENEW_URL` | ✅ | 续期页面 URL，如 `https://panel.host2play.net/server/renew?i=xxx` |
| `H2P_COOKIE` | ✅ | host2play 的 cookie 字符串 |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token（要通知才填） |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |

#### 方式 B：多账号（推荐）

| Secret 名 | 必填 | 说明 |
|---|---|---|
| `H2P_ACCOUNTS` | ✅ | 多账号配置，每行一个：`名称|||续期URL|||Cookie` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |

**`H2P_ACCOUNTS` 格式示例**：

```
我的服务器1|||https://panel.host2play.net/server/renew?i=aaa|||session=eyJpdi...; XSRF-TOKEN=eyJpdi...
我的服务器2|||https://panel.host2play.net/server/renew?i=bbb|||session=eyJpdi...; XSRF-TOKEN=eyJpdi...
我的服务器3|||https://panel.host2play.net/server/renew?i=ccc|||session=eyJpdi...; XSRF-TOKEN=eyJpdi...
```

> 字段用 `|||`（三个竖线）分隔，因为 Cookie 里常含 `;` 和 `=`，用逗号会冲突。
> 
> 也可以省略名称，只写 `URL|||Cookie`，脚本会用 `server-1` / `server-2` 自动命名。

### （可选）配置代理

如需使用 Hysteria2 住宅代理规避触发验证：
- 设置环境变量 `H2P_HYSTERIA2_PROXY=hysteria2://...` 或直接设置 `H2P_WARP_PROXY=socks5://127.0.0.1:10800`

### 2. 获取 Cookie

1. 浏览器登录 `https://panel.host2play.net`
2. 按 F12 → Application → Cookies → `https://panel.host2play.net`
3. 把所有 cookie 按 `Name=Value; ` 格式拼接

或者用 Cookie-Editor 插件一键导出（Header String 格式）。

### 3. 手动触发测试

`Actions` → `host2play 续期` → `Run workflow`

### 4. 自动续期

默认 cron：
- UTC `00:00, 11:00, 22:00` = 北京 `08:00, 19:00, 06:00`
- UTC `05:30, 16:30` = 北京 `13:30, 00:30`

每天 5 次，足够维持续期。

## 📱 TG 通知示例

**启动通知**：
```
🎮 Host2Play 续期
🚀 续期启动
⏰ 2026-07-16 16:00:00 (北京)
👥 共 3 个账号
```

**结果汇总**（多账号一次性发送）：
```
🎮 Host2Play 续期
⏰ 2026-07-16 16:05:00 (北京)

📊 总账号: 3 | ✅ 2 | ❌ 1

👤 我的服务器1: ✅ 7h 57m → 31h 57m (+24h 0m)
👤 我的服务器2: ✅ 12h 0m → 36h 0m (+24h 0m)
👤 我的服务器3: ❌ 续期失败
```

## ⚠️ 注意事项

1. **必须用公开仓库**：私有仓库 GHA 分钟数不够
2. **Cookie 有效期**：一般 7-30 天，过期需重新复制
3. **SeleniumBase UC 模式**：内置反检测，绕过大多数网站识别
4. **视频广告处理**：如有广告，脚本会自动等待或跳过

## 🐛 故障排查

| 问题 | 解决 |
|---|---|
| `ImportError: No module named seleniumbase` | 检查 requirements.txt 是否已更新为 seleniumbase |
| Cookie 失效 | 重新复制 cookie 更新 `H2P_COOKIE` |
| 续期页面打不开 | 检查网络连接及代理配置 (`H2P_HYSTERIA2_PROXY`) |
| 找不到 Renew 按钮 | 检查 `H2P_RENEW_URL` 是否正确 |
| Telegram 未收到通知 | 确认 `TG_BOT_TOKEN` 和 `TG_CHAT_ID` 配置正确 |

## 📄 License

MIT

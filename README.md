# 自动续期合集 🎮

多个免费 Minecraft / VPS 面板的 GitHub Actions 自动续期合集。

> 所有方案都用 GitHub Actions（公开仓库免费），无需 VPS。

---

## 📦 已支持面板

| 面板 | 目录 | 登录方式 | 备注 |
| --- | --- | --- | --- |
| [ACLClouds](https://dash.aclclouds.com/projects) | [`ACLClouds-server/`](ACLClouds-server/) | Cookie 注入 | 纯 API, 无浏览器 |
| [Gaming4Free](https://control.gaming4free.net/) | [`gaming4free-renew/`](gaming4free-renew/) | Cookie 注入 | SeleniumBase UC + Turnstile |
| [Host2Play](https://panel.host2play.net/) | [`host2play-renew/`](host2play-renew/) | Cookie 注入 | SeleniumBase UC + Hysteria2 |

---

## 🚀 通用部署流程

每个面板都需要在仓库 **Settings → Secrets and variables → Actions** 配置：

- 各自的 Cookie / 账号信息（见各目录 README）
- `TG_BOT_TOKEN` + `TG_CHAT_ID`（接收续期通知，可选但推荐）

配置完成后，进入 **Actions** 标签手动 Run 一次测试，通过后定时任务会自动跑。

---

## 🔑 各面板 Secrets 一览

### ACLClouds (`aclclouds-kaka.yml`)

| Secret 名 | 必填 | 说明 |
| --- | --- | --- |
| `ACL_COOKIES` | ✅ | 单账号 Cookie 字符串 |
| `ACL_ACCOUNTS` | 多账号 | 每行 `名称\|\|\|Cookie` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |

### Gaming4Free (`gaming4free.yml`)

| Secret 名 | 必填 | 说明 |
| --- | --- | --- |
| `GAME4FREE_RENEW_URL` | ✅ | 续期页面 URL（单账号） |
| `GAME4FREE_COOKIE` | ✅ | Cookie 字符串（单账号） |
| `GAME4FREE_ACCOUNTS` | 多账号 | 每行 `名称\|\|\|URL\|\|\|Cookie` |
| `PROXY_URL` | ✅ | sing-box 节点链接（tuic/vless/vmess 等） |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |

### Host2Play (`host2play.yml`)

| Secret 名 | 必填 | 说明 |
| --- | --- | --- |
| `H2P_RENEW_URL` | ✅ | 续期页面 URL（单账号） |
| `H2P_COOKIE` | ✅ | Cookie 字符串（单账号） |
| `H2P_ACCOUNTS` | 多账号 | 每行 `名称\|\|\|URL\|\|\|Cookie` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |

---

## 📁 目录结构

```
.
├── .github/workflows/
│   ├── aclclouds-kaka.yml    # ACLClouds 续期 (每天 UTC 03:00 / 15:00)
│   ├── gaming4free.yml       # Gaming4Free 续期 (每天 UTC 01:00)
│   └── host2play.yml         # Host2Play 续期 (每天 5 次)
├── ACLClouds-server/         # ACLClouds 续期脚本 (纯 API)
├── gaming4free-renew/        # Gaming4Free 续期脚本 (SeleniumBase UC)
└── host2play-renew/          # Host2Play 续期脚本 (SeleniumBase UC + Hysteria2)
```

---

## 📄 License

MIT

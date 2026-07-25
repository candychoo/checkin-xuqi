# Gaming4Free 自动续期

GitHub Actions + SeleniumBase UC mode + sing-box 代理 + Cloudflare Turnstile 破解

## 文件结构

```
gaming4free-renew/
├── renew.py          # 主脚本（多服务器支持）
├── cfg.py            # 配置解析
├── util.py           # Livewire JS 工具
├── tg.py             # Telegram 通知
├── cd.py             # Cooldown 检测
├── requirements.txt  # Python 依赖
└── README.md         # 本文档
```

## 工作原理

1. **sing-box 代理** — GitHub Actions 中启动 sing-box，Chrome 走 SOCKS5 出口（CF 自家 IP，必过 Turnstile）
2. **SeleniumBase UC mode** — 反检测浏览器，绕过 Cloudflare 5 秒盾
3. **Turnstile 破解** — 物理点击 iframe 内 checkbox + 等待 token 返回
4. **多服务器续期** — `SERVERS` 环境变量配置多个服务器，逐个续期并截图通知
5. **时间验证** — 续期前后对比剩余时间，确保真成功

## 部署步骤

### 1. 配置 Secrets

在 GitHub Repository → Settings → Secrets and variables → Actions 中添加：

| Secret | 必填 | 说明 |
|---|---|---|
| `PROXY_URL` | ✅ | 代理 URL（sing-box setup_proxy.sh 自动生成，需配置） |
| `TG_BOT_TOKEN` | ✅ | Telegram Bot Token |
| `TG_CHAT_ID` | ✅ | Telegram Chat ID |
| `GAME4FREE_COOKIE` | ✅ | Cookie 字符串（分号分隔） |
| `GAME4FREE_RENEW_URL` | ❌ | 自定义续期页面 URL（默认 `https://gaming4free.zapto.org/`） |
| `GAME4FREE_ACCOUNTS` | ❌ | 多账号格式：`名称|||URL|||Cookie` |
| `SERVERS` | ✅ | **服务器列表**，格式 `编号,地区\|编号,地区`，如 `1,US\|2,CN\|3,EU` |

### 2. 配置代理（可选但推荐）

在 workflow 的 "配置代理" 步骤前添加代理配置 secret。sing-box 会自动启动并监听 `socks5://127.0.0.1:1080`。

### 3. 获取 Cookie

1. 浏览器登录 [Gaming4Free](https://control.gaming4free.net)
2. F12 → Application → Cookies → 复制所有 Cookie 值（分号分隔）
3. 粘贴到 `GAME4FREE_COOKIE` secret

### 4. 配置服务器列表

在 `SERVERS` secret 中填写要续期的服务器，格式：

```
编号,地区|编号,地区|编号,地区
```

示例：
- `1,US` — 只续期 1 号美国服务器
- `1,US|2,CN|3,EU` — 续期 1 号美国、2 号中国、3 号欧洲

## 运行方式

### GitHub Actions（自动）

- **手动触发**：Actions tab → "Game4Free 自动续期" → Run workflow
- **定时触发**：每天 UTC 01:00（北京时间 09:00）自动运行

### 本地运行

```bash
cd gaming4free-renew
pip install -r requirements.txt
xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" python renew.py
```

## 输出

- **Telegram 通知** — 每次续期结果（成功/失败 + 截图）
- **Debug 截图** — GitHub Actions artifacts 中保存 `artifacts/` 目录下的截图

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `ERR_NAME_NOT_RESOLVED` | 没有代理，DNS 无法解析 | 确认 sing-box 已启动 |
| `ERR_CONNECTION_RESET` | 代理格式不对 | 检查 `PROXY_URL` 环境变量 |
| `Browser not ready` | UC mode 启动超时 | 增加 workflow timeout |
| Turnstile 一直转圈 | 代理 IP 被 CF 识别 | 更换代理或使用 CF 自家 IP |
| 续期后时间没增加 | 按钮未真正点击 / Cookie 过期 | 重新获取 Cookie |

# ACLClouds 自动续期

为 <https://aclclouds.com/dashboard/projects> 上的免费 Minecraft / VPS 服务器自动续期。

## 两种续期方案

### 方案一: 纯 API (renew.py) - 推荐, 但需 Turnstile

- 直接调用 API: `POST /api/client/servers/{id}/upgrade/renew`
- 通过 Cookie 注入保持登录态
- **问题**: 续期 API 需要 Cloudflare Turnstile 验证, 纯 API 无法通过
- 适合: 续期 API 不需要验证的情况 (或验证已解除)

### 方案二: 浏览器版 (renew_browser.py) - 推荐, 能过 Turnstile

- 用 SeleniumBase UC mode 打开浏览器
- 复用 Gaming4Free 的 CF Turnstile 破解方案 (CDP 点击 checkbox)
- 先用 API 获取服务器列表 (快), 再用浏览器续期 (能过验证)
- 适合: 续期 API 需要 Turnstile 验证的情况

## 部署步骤

### 1. Fork / Clone 本仓库到你的 GitHub

### 2. 获取 Cookie

1. 用浏览器登录 <https://aclclouds.com>
2. 按 `F12` → `Application` → `Cookies` → `https://aclclouds.com`
3. 复制全部 Cookie 为字符串 (格式: `key1=value1; key2=value2; ...`)

> 必须包含: `XSRF-TOKEN` 和 `__Host-aclclouds_session` (或 `aclclouds_session`)

### 3. 配置 GitHub Secrets

| Secret | 必填 | 说明 |
| --- | --- | --- |
| `ACL_COOKIES` | ✅ | 单账号 Cookie 字符串 |
| `ACL_ACCOUNTS` | 多账号 | 每行 `name\|\|\|cookie` |
| `PROXY_URL` | 浏览器版推荐 | sing-box 节点链接 (hysteria2/tuic/vless) |
| `TG_BOT_TOKEN` | 通知 | Telegram Bot Token |
| `TG_CHAT_ID` | 通知 | Chat ID |

### 4. 选择 Workflow

| Workflow | 脚本 | 适用 |
| --- | --- | --- |
| `ACLClouds-卡卡续期` | `renew.py` | 纯 API, 快, 但过不了 Turnstile |
| `ACLClouds-浏览器续期` | `renew_browser.py` | 浏览器版, 能过 Turnstile |

进入 `Actions` → 选择对应 workflow → `Run workflow`

## 续期规则

| 服务类型 | 可续期阈值 |
| --- | --- |
| 免费服务 (普通) | 到期前 2 天 |
| 免费 Minecraft | 到期前 2 小时 |
| 付费服务 | 4 天前 |

脚本默认 `< 48h` 就尝试续期, 后端会返回 `renewNotAvailableYet` 跳过。

## 浏览器版工作流程

1. 用 API 获取服务器列表 + 到期时间 (快)
2. 筛选剩余 < 48h 的服务器
3. 启动 SeleniumBase UC mode 浏览器
4. 注入 Cookie (自动改名 `aclclouds_session` → `__Host-aclclouds_session`)
5. 对每个服务器:
   - 打开 `/server/{id}` 页面
   - 找续期按钮 (多种选择器 + JS 兜底)
   - 点击后处理 CF Turnstile (CDP 点击 checkbox)
   - 检查续期结果 (toast 通知)
6. TG 通知汇总

## 本地调试

```bash
# 纯 API 版
pip install -r requirements.txt
export ACL_COOKIES="XSRF-TOKEN=...; aclclouds_session=..."
python renew.py

# 浏览器版
pip install -r requirements.txt
export ACL_COOKIES="XSRF-TOKEN=...; aclclouds_session=..."
xvfb-run --auto-servernum python renew_browser.py
```

## TG 通知示例

```
🎮 ACLClouds 续期汇总 [main]
✅ 成功: 2 | ❌ 失败: 0
📊 总计: 2 台服务器
```

## 维护

- Cookie 有效期约 7-30 天, 过期后重新登录复制
- 浏览器版需要代理 (PROXY_URL), 否则 CF 验证可能过不去
- 阈值: 修改 `RENEW_THRESHOLD_HOURS` 环境变量 (默认 48h)


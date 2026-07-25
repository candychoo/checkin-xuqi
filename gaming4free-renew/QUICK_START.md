# Gaming4Free Pro 续期脚本 - 快速修复指南

## 📋 修复概述

**问题**：续期失败，时间反而减少（-189秒）
**原因**：验证弹窗（Google reCAPTCHA/Cloudflare Turnstile）干扰时间检测
**解决方案**：新增验证弹窗检测与等待逻辑

---

## 🚀 快速修复（3 步）

### 步骤 1：运行修复脚本
```bash
cd /path/to/gaming4free-renew
./apply_fix.sh
```

### 步骤 2：测试脚本
```bash
# 仅检查配置和语法
./test_fix.sh

# 运行实际测试
xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" python3 renew.py
```

### 步骤 3：查看结果
```bash
# 实时查看日志
tail -f debug_output/*.log
```

---

## 📱 配置 Telegram 通知（可选）

如果需要接收续期成功/失败的 Telegram 通知：

### 快速配置（推荐）
```bash
# 1. 运行配置脚本
./setup_tg.sh

# 2. 编辑配置文件，填入你的 Bot Token 和 Chat ID
nano .env
# 或
vi .env

# 3. 测试通知
./test_tg.sh

# 4. 运行脚本
xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" python3 renew.py
```

### 手动配置
```bash
# 设置环境变量
export TG_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
export TG_CHAT_ID="123456789"

# 验证配置
./test_tg.sh

# 运行脚本
xvfb-run --auto-servernum --server-args="-screen 0 1920x1080x24" python3 renew.py
```

**详细说明**：[TG_SETUP.md](./TG_SETUP.md)

---

## 📁 修复文件清单

| 文件 | 说明 |
|------|------|
| `renew_fixed.py` | 修复版脚本（新增验证弹窗处理） |
| `renew.py.bak` | 原脚本备份（自动生成） |
| `apply_fix.sh` | 一键应用修复脚本 |
| `test_fix.sh` | 测试脚本（检查环境和语法） |
| `setup_tg.sh` | TG 配置快速设置脚本 |
| `test_tg.sh` | TG 通知测试脚本 |
| `TG_SETUP.md` | Telegram 通知详细配置指南 |
| `REPAIR_NOTES.md` | 详细修复说明文档 |

---

## 🔧 主要修复内容

### 1. 新增验证弹窗检测
```python
def is_verify_human_state(drv) -> bool:
    """检测是否出现 'Verify you're human' 验证弹窗"""
```

### 2. 新增验证弹窗等待
```python
def wait_for_verify_dialog(drv, max_wait: int = 30, retry: int = 3) -> bool:
    """等待验证弹窗消失（Google reCAPTCHA / Cloudflare Turnstile 等）"""
```

### 3. 优化续期等待逻辑
- 等待验证弹窗消失后再检测时间
- 增加等待时间到 60 秒
- 检测验证弹窗是否再次出现

---

## 📊 修复效果对比

### 修复前
```
[06:46:44] ✅ 可点击按钮: + 90 min
[06:46:45] 🖱️ 点击按钮: + 90 min
[06:47:47] ⏳ 等待续期生效...
[06:48:53] ❌ 续期失败，增量不足: -189s  ← 读取到验证弹窗中的旧时间
```

### 修复后（预期）
```
[06:46:44] ✅ 可点击按钮: + 90 min
[06:46:45] 🖱️ 点击按钮: + 90 min
[06:46:46] ⏳ 等待验证弹窗消失...
[06:47:16] ✅ 验证弹窗已消失
[06:47:16] ⏳ 等待续期生效...
[06:48:16] ✅ 检测到时间增加: +5400 秒 (90 分)
[06:48:16] 🎉 续期成功! +90 分钟
```

---

## ⚙️ 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `VERIFY_WAIT` | 30 | 验证弹窗等待时间（秒） |
| `VERIFY_RETRY` | 3 | 验证弹窗等待重试次数 |
| `CLICK_DELAY` | 1.5 | 点击后等待时间（秒） |
| `MAX_ROUNDS` | 10 | 最大续期轮次 |

---

## 🛠️ 故障排查

### 问题1：验证弹窗一直不消失
**解决方案**：
```bash
# 编辑 renew.py，修改以下配置
VERIFY_WAIT = 60      # 增加到 60 秒
VERIFY_RETRY = 5      # 增加到 5 次
```

### 问题2：脚本提前终止
**解决方案**：
```bash
# 编辑 renew.py，修改以下配置
MAX_ZERO_DIFF_ROUNDS = 3  # 增加到 3 轮
```

### 问题3：回滚到原版本
```bash
cd /path/to/gaming4free-renew
mv renew.py.bak renew.py
```

---

## 📝 日志说明

### 正常日志示例
```
[2026-07-25 06:46:44] 📋 找到可点击按钮: + 90 min
[2026-07-25 06:46:45] 🖱️ 点击按钮: + 90 min
[2026-07-25 06:46:46] ⏳ 等待验证弹窗消失...
[2026-07-25 06:47:16] ✅ 验证弹窗已消失
[2026-07-25 06:47:16] ⏳ 等待续期生效...
[2026-07-25 06:48:16] ✅ 检测到时间增加: +5400 秒 (90 分)
[2026-07-25 06:48:16] 🎉 续期成功! +90 分钟
```

### 错误日志示例
```
[2026-07-25 06:48:53] ❌ 续期失败，增量不足: -189s
[2026-07-25 06:48:53] ❌ 连续 2 轮增量<=0，判定已达上限，结束该账号
```

---

## 📞 技术支持

如遇问题，请提供以下信息：
1. 完整日志文件（`debug_output/*.log`）
2. 脚本版本（`grep "v\." renew.py`）
3. 配置文件内容（`cat cfg.py`）

---

## 📄 相关文档

- [详细修复说明](./REPAIR_NOTES.md)
- [脚本源码](./renew_fixed.py)
- [配置文件](./cfg.py)

---

**修复版本**：v33
**修复日期**：2026-07-25
**状态**：✅ 已修复

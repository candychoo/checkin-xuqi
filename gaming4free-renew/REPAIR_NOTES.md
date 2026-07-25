# Gaming4Free Pro 续期脚本修复说明

## 问题描述

运行日志显示续期失败，时间反而减少（-189秒）：
- 点击 "+ 90 min" 按钮后，剩余时间从 05:53:19 变为 05:50:10
- 页面弹出 "Verify you're human to continue" 验证弹窗
- 连续两轮失败后脚本提前终止

## 根本原因分析

### 1. 验证弹窗干扰
点击续期按钮后，Google reCAPTCHA 或 Cloudflare Turnstile 验证弹窗立即出现，遮挡了续期结果展示。脚本在验证弹窗存在的情况下尝试读取剩余时间，获取到的是**验证页面上的旧时间**（而非实际续期后的时间），导致检测到时间减少。

### 2. 时间检测逻辑缺陷
原脚本在验证弹窗消失前就尝试读取时间：
```python
# 原逻辑：等待 30 秒后立即检查时间
wait_end = time.time() + 30
while time.time() < wait_end:
    time.sleep(3)
    _, cur_sec = get_remaining_seconds(drv)  # 可能读取到验证弹窗中的旧时间
    if cur_sec > pre_sec + 300:
        success = True
```

### 3. 验证弹窗处理缺失
脚本没有检测和等待验证弹窗消失的逻辑，导致：
- 在验证弹窗中读取到错误的时间
- 验证弹窗可能阻塞后续操作

---

## 修复方案

### 1. 新增验证弹窗检测函数
```python
def is_verify_human_state(drv) -> bool:
    """检测是否出现 'Verify you're human' 验证弹窗"""
    body = drv.execute_script("return document.body ? document.body.innerText : '';")
    body_lower = body.lower()
    verify_patterns = [
        'verify you are human',
        'verify you\'re human',
        'check you are human',
        'i am not a robot',
        'human verification',
        'please wait',
    ]
    for pat in verify_patterns:
        if pat in body_lower:
            return True
    return False
```

### 2. 新增验证弹窗等待函数
```python
def wait_for_verify_dialog(drv, max_wait: int = 30, retry: int = 3) -> bool:
    """等待验证弹窗消失（Google reCAPTCHA / Cloudflare Turnstile 等）"""
    start = time.time()
    attempt = 0

    while attempt < retry:
        attempt += 1
        time_left = max(0, max_wait - int(time.time() - start))

        if is_verify_human_state(drv):
            log(f"验证弹窗仍存在，等待中... ({attempt}/{retry}, 剩余 {time_left}s)", "WAIT")

            # 如果等待时间足够长，尝试刷新页面（重新触发验证）
            if attempt >= 2 and time_left > 15:
                log("等待超时，尝试刷新页面...", "WARN")
                try:
                    drv.refresh()
                    time.sleep(5)
                except:
                    pass

            if time_left > 0:
                time.sleep(3)
            else:
                log("验证弹窗等待超时", "ERR")
                return False
        else:
            log(f"验证弹窗已消失", "OK")
            return True

    log(f"验证弹窗等待失败（尝试 {retry} 次）", "ERR")
    return False
```

### 3. 优化续期等待逻辑
```python
# 点击按钮后，先等待验证弹窗消失
log("等待验证弹窗消失...", "WAIT")
if not wait_for_verify_dialog(drv):
    log("验证弹窗等待失败，跳过本轮", "WARN")
    time.sleep(10)
    try:
        drv.refresh()
        time.sleep(3)
    except:
        pass
    continue

# 再等待续期生效（增加到 60 秒）
log("等待续期生效...", "WAIT")
success = False
wait_end = time.time() + 60  # 增加到 60 秒
while time.time() < wait_end:
    time.sleep(3)

    # 检查验证弹窗是否再次出现
    if is_verify_human_state(drv):
        log("验证弹窗再次出现，继续等待...", "WAIT")
        continue

    _, cur_sec = get_remaining_seconds(drv)
    if cur_sec > pre_sec + 300:  # 至少增加 5 分钟
        diff = cur_sec - pre_sec
        log(f"检测到时间增加: +{diff} 秒 ({diff//60} 分)", "OK")
        success = True
        break
```

### 4. 增加配置项
```python
VERIFY_WAIT = 30               # 验证弹窗等待时间（秒）
VERIFY_RETRY = 3               # 验证弹窗等待重试次数
```

---

## 修复效果

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

## 使用方法

### 1. 备份原脚本
```bash
cd /path/to/gaming4free-renew
cp renew.py renew.py.bak
```

### 2. 使用修复版本
```bash
# 直接运行修复版本
python3 renew_fixed.py

# 或者重命名后运行
mv renew_fixed.py renew.py
python3 renew.py
```

### 3. 调试模式（可选）
如需查看浏览器界面：
```bash
# 修改 HEADLESS = False
HEADLESS = False

# 然后运行
python3 renew_fixed.py
```

---

## 配置说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| VERIFY_WAIT | 30 | 验证弹窗等待时间（秒） |
| VERIFY_RETRY | 3 | 验证弹窗等待重试次数 |
| CLICK_DELAY | 1.5 | 点击后等待时间（秒） |
| BUTTON_RETRY_REFRESH | True | 按钮未找到时是否刷新重试 |
| MAX_ROUNDS | 10 | 最大续期轮次 |

---

## 注意事项

1. **验证弹窗可能需要更长时间**：如果验证弹窗持续出现，可以增加 `VERIFY_WAIT` 和 `VERIFY_RETRY`
2. **代理稳定性**：确保代理正常工作，否则验证弹窗可能无法通过
3. **Cookie 有效期**：定期更新 Cookie，避免登录失效
4. **会话上限保护**：脚本会自动检测 48h 会话上限，避免无效操作

---

## 故障排查

### 问题1：验证弹窗一直不消失
**解决方案**：
- 检查代理是否正常工作
- 增加 `VERIFY_WAIT`（如改为 60 秒）
- 增加 `VERIFY_RETRY`（如改为 5 次）
- 尝试手动完成验证（Headless 模式下可临时关闭）

### 问题2：续期成功但时间未增加
**解决方案**：
- 检查是否在验证弹窗中读取时间（查看日志中的 "验证弹窗仍存在"）
- 增加 `VERIFY_WAIT` 和等待时间
- 检查 Cookie 是否有效

### 问题3：脚本提前终止
**解决方案**：
- 检查 `MAX_ZERO_DIFF_ROUNDS` 配置（默认 2 轮）
- 查看日志中的具体错误信息
- 确认账号状态正常

---

## 版本历史

- **v33**（修复版）：新增验证弹窗检测与等待逻辑；优化续期等待时间；增加重试机制
- **v32**：增加会话上限检测；连续失败自动停止；按钮未找到时刷新重试
- **v31**：修复时间检测逻辑；增加代理支持

# 设计：屏幕外有头窗口自动通过 Cloudflare 验证

- 日期：2026-08-12
- 状态：已批准
- 范围：`config.py`、`screenshot.py`、`README_RUN.md`、`CLAUDE.md`

## 设计变更（2026-08-12 用户决策，实施后追加）

实施中实测发现原"headless 快路径"对本站不成立：`cf_clearance` 与获取它的浏览器指纹绑定，
headless 用不了有头缓存的 cookie，对本类站基本必被拦（两次 `--once` 都先空等 15s 再回退）。

**用户选择：直接走屏幕外有头（推荐）。** 因此 `_capture()` 不再 headless 优先，默认直接
`_capture_once(headless=False)`（配 `CF_HEADED_OFFSCREEN=True` 即屏幕外窗口、无感自动通过）；
headless 仅作调试路径（`config.CF_FORCE_HEADLESS=True` 时先试）。原 `CF_FORCE_HEADED` 开关
移除，由 `CF_FORCE_HEADLESS` 取代。数据流、文档（README_RUN.md / CLAUDE.md）均已按新设计更新。

## 背景与问题

目标站 Downdetector 受 Cloudflare 保护。经实测（2026-08-12，三次对照）：

| 用例 | 结果 |
|---|---|
| 有头（真实 Chromium，全新 profile，窗口已移出屏幕外） | 秒过，无需点击，直接加载真实页面 |
| headless + 完整版 Chromium + `--headless=new` | 验证页 60s 不自动消失 |
| headless 默认内核 | 验证页 60s 不自动消失 |

**结论：**
1. 该站是**托管型挑战**（managed challenge，非交互式）——真实浏览器自动放行，不需要点 Turnstile checkbox。
2. 拦路的不是"该不该点击"，而是 **Cloudflare 能识别 headless 模式**：任何 headless 变体首访都过不去；有头真实 Chromium 首访直接过。
3. 验证放行**不依赖窗口是否可见/在前台**——屏幕外窗口同样秒过。

因此旧实现（README_RUN.md）的假设是反的：它默认 headless 无感、被拦才回退有头**等手动点击**。实际上 headless 首访必被拦，而有头模式自动通过、根本不需要人点。

## 方案

保留"headless 优先 + 回退有头"的两条腿结构，但把回退腿从"等手动点"改为**屏幕外有头窗口自动通过**：

- **快路径（默认）**：headless，复用 `.browser_profile` 缓存的 `cf_clearance` cookie。cookie 有效期内无感秒截，零窗口。
- **回退路径**：headless 被拦时，启动真实 Chromium 有头窗口但**移出屏幕外**（`--window-position=-32000,-32000`），托管型挑战几秒内自动放行，cookie 写入持久化 profile 后关闭。全程零人工、人不可见。

## 改动明细

### 1. `config.py`

新增开关：

```python
CF_HEADED_OFFSCREEN = True   # 回退窗口移到屏幕外（默认 True）；设 False 则可见，用于调试
```

更新 Cloudflare 注释为实测结论：headless 首访必被拦；有头真实 Chromium 自动通过托管型挑战，无需手动点。

### 2. `screenshot.py`（核心改动，约 20-30 行）

- `_capture_once()`：当 `headless=False` 且 `CF_HEADED_OFFSCREEN=True` 时，向 `launch_persistent_context` 的 `args` 追加 `--window-position=-32000,-32000`。
- `_capture()`：逻辑不变（headless 优先 → 失败回退有头），回退腿现在自动通过。
- 保留 `_try_click_turnstile()` 作为最后兜底（若目标站换成交互式验证仍保有既有能力），不新增复杂度。

### 3. `monitor.py`

无需改动（`--baseline` / `--once` / 循环均经 `capture_with_text()`）。

### 4. 文档

- `README_RUN.md`：把"手动点验证框"改为"屏幕外有头窗口自动通过"，记录实测结论。
- `CLAUDE.md`：同步更新 Cloudflare 处理一节。

## 数据流

```
每次巡检 capture_with_text()
 ├─ 快路径: headless（CF_HEADLESS_TIMEOUT=15s 内验证自动消失则继续截图）
 │    └─ cookie 有效时秒过，无任何窗口
 └─ 回退: headless 被拦 → 屏幕外有头窗口（CF_HEADED_TIMEOUT=120s 内自动通过）
      └─ 通过后 cookie 写入 .browser_profile，随后窗口关闭
```

首次运行与每次 cookie 过期，均只会静默触发一次屏幕外窗口。

## 错误处理

- 屏幕外有头窗口超时仍不过 → 记录明确错误日志 + 按当前状态兜底截图（保持现有行为）。
- 不新增异常路径。

## 验证方式（无测试框架，手动）

1. `python monitor.py --baseline`（首次，`.browser_profile` 为空）→ 静默自动过验证生成基准图。
2. `python monitor.py --once` 连跑 2-3 次 → 确认 cookie 缓存后走 headless 无感。
3. `CF_FORCE_HEADED=True` 临时强制 → 验证屏幕外有头路径独立可用。

## 不在范围内

- 交互式 Turnstile（需人工点击的站点）：本方案不保证，仅保留既有点击兜底。
- FlareSolverr / 付费打码：实测证明本场景不需要。
- 引入测试框架 / 重构其它模块。

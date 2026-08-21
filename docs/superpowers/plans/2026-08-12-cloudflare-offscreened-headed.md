# Cloudflare 屏幕外有头窗口自动通过 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让监控工具在 headless 首访被 Cloudflare 拦下时，自动用"屏幕外有头窗口"刷新 cookie 并通过托管型挑战，全程零人工、零可见窗口。

**Architecture:** 保留现有"headless 优先 → 被拦回退有头"两条腿结构。headless 快路径不变（cookie 有效时无感）；回退腿改为在有头模式时给 Chromium 追加 `--window-position=-32000,-32000` 把窗口移到屏幕外，managed challenge 自动通过后 cookie 写入 `.browser_profile`。核心改动只在 `screenshot.py` 一个函数。

**Tech Stack:** Python 3.13、Patchright（patchright.sync_api）、持久化浏览器 profile。无测试框架，验证用 CLI 实跑。

> **计划变更（2026-08-12 用户决策）**：Task 3 实测发现 headless 快路径对本站必被拦
> （`cf_clearance` 与浏览器指纹绑定），用户选择"直接走屏幕外有头"。`_capture()` 改为默认直接
> 屏幕外有头；`CF_FORCE_HEADED` 移除、由 `CF_FORCE_HEADLESS`（默认 False，调试用）取代。
> Task 3/Task 4 的描述按此偏差执行，文档已同步。

## Global Constraints

- 本项目无测试框架、无 linter/formatter（见 CLAUDE.md）。验证一律用 CLI 实跑，不引入 pytest。
- 仓库**不是 git 仓库**：无 commit 步骤；若已 `git init`，每个 Task 末尾的 checkpoint 可自行提交。
- 本机 bash 的 `python` 被 Microsoft Store stub 抢占，统一用完整路径：
  `"/c/Users/Test/AppData/Local/Programs/Python/Python313/python.exe"`
- 必须用 Patchright（`import patchright`），永远不要 `playwright`；不要再加 `playwright-stealth`。
- **永远不要删除 `.browser_profile/`**；本次实现只在首次运行时由脚本自动创建。
- 代码注释、日志、文档用中文（与代码库一致）。
- 改动最小化：只动 `config.py` / `screenshot.py` / `README_RUN.md` / `CLAUDE.md`，不重构其它模块。

---

### Task 1: config.py 增加屏幕外开关

**Files:**
- Modify: `config.py`（Cloudflare 处理一节）

**Interfaces:**
- Produces: `config.CF_HEADED_OFFSCREEN: bool = True`（后续 Task 2 读取）

- [ ] **Step 1: 修改 config.py 的 Cloudflare 一节**

把下面的代码块：

```python
# ──────────────────────────────────────────────
# Cloudflare 处理
# ──────────────────────────────────────────────
# 持久化浏览器配置会自动缓存 cf_clearance，正常情况下无感通过。
CF_FORCE_HEADED = False                     # True = 始终有头（调试用）；False = 默认 headless，被拦截才回退有头
CF_HEADLESS_TIMEOUT = 15                    # headless 下等待 CF 自动通过的秒数
CF_HEADED_TIMEOUT = 120                     # 有头模式下等待 CF 通过的秒数（含手动点击时间）
```

替换为：

```python
# ──────────────────────────────────────────────
# Cloudflare 处理
# ──────────────────────────────────────────────
# 实测结论（2026-08-12）：目标站为托管型挑战（managed challenge，非交互式）——
# headless 首访必被拦；而有头真实 Chromium 自动通过、无需人工点击。
# 因此：默认 headless 快路径 + 回退"屏幕外有头窗口"自动刷新 cookie，全程无感。
CF_FORCE_HEADED = False                     # True = 始终有头（调试用）；False = 默认 headless，被拦截才回退有头
CF_HEADED_OFFSCREEN = True                  # True = 回退窗口移到屏幕外（默认，无感）；False = 可见窗口（调试用）
CF_HEADLESS_TIMEOUT = 15                    # headless 下等待 CF 自动通过的秒数
CF_HEADED_TIMEOUT = 120                     # 有头模式等待 CF 自动通过的秒数
```

- [ ] **Step 2: 验证 config 可导入且开关存在**

运行：
```bash
cd "C:\Users\Test\Desktop\code" && "/c/Users/Test/AppData/Local/Programs/Python/Python313/python.exe" -c "import config; assert config.CF_HEADED_OFFSCREEN is True; print('Task1 OK')"
```
期望：输出 `Task1 OK`。

- [ ] **Step 3: checkpoint**

变更完成。若已 `git init`，可提交：
```bash
git add config.py && git commit -m "feat(config): add CF_HEADED_OFFSCREEN switch"
```

---

### Task 2: screenshot.py 回退窗口移到屏幕外

**Files:**
- Modify: `screenshot.py`（`_capture_once` 的 `launch_persistent_context` 调用 + 有头失败日志文案）

**Interfaces:**
- Consumes: `config.CF_HEADED_OFFSCREEN`（Task 1）
- Produces: 无新接口；`_capture()` 回退路径行为改变——有头时窗口在屏幕外，managed challenge 自动通过。

- [ ] **Step 1: 改 `_capture_once` 的启动参数**

把这一段：

```python
        context = p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE_DIR,
            headless=headless,
            viewport={
                "width": config.VIEWPORT_WIDTH,
                "height": config.VIEWPORT_HEIGHT,
            },
            args=[
                "--no-sandbox",
            ],
        )
```

替换为：

```python
        launch_args = ["--no-sandbox"]
        if not headless and getattr(config, "CF_HEADED_OFFSCREEN", True):
            # 回退窗口移到屏幕外：托管型挑战会自动通过，无需人工点击、不打扰用户。
            launch_args.append("--window-position=-32000,-32000")

        context = p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE_DIR,
            headless=headless,
            viewport={
                "width": config.VIEWPORT_WIDTH,
                "height": config.VIEWPORT_HEIGHT,
            },
            args=launch_args,
        )
```

- [ ] **Step 2: 更新有头失败日志文案**

把这一段：

```python
                        logger.error(
                            "有头模式仍未通过 Cloudflare，请在弹出的窗口里手动点一下验证框"
                        )
```

替换为：

```python
                        logger.error(
                            "有头模式仍未通过 Cloudflare（可能网络异常或该站为交互式验证）"
                        )
```

（旧文案误导：窗口已在屏幕外，不存在"弹出来手动点"。）

- [ ] **Step 3: 语法检查**

运行：
```bash
cd "C:\Users\Test\Desktop\code" && "/c/Users/Test/AppData/Local/Programs/Python/Python313/python.exe" -m py_compile config.py screenshot.py && echo "Task2 syntax OK"
```
期望：输出 `Task2 syntax OK`。

- [ ] **Step 4: checkpoint**

变更完成。若已 `git init`，可提交：
```bash
git add screenshot.py && git commit -m "feat(screenshot): move headed fallback window off-screen"
```

---

### Task 3: 端到端验证（真实跑通）

**Files:**
- 无源码改动。使用现有 `monitor.py --baseline` / `--once`。

**前置条件：** 确认 `.browser_profile/` 不存在（本机目前即不存在，首次运行才会创建）。若存在且想重测"首访"，需在删除前征得用户同意——**不要自行删除**。

- [ ] **Step 1: 清掉旧基准图（本机 baseline 目录当前为空，无需操作；若已存在请先确认再删）**

运行：
```bash
cd "C:\Users\Test\Desktop\code" && ls baseline/ 2>/dev/null
```
若存在 `normal.png`，先与用户确认后删除：`rm baseline/normal.png`。

- [ ] **Step 2: 首访生成基准图（应触发屏幕外有头路径自动过验证）**

运行：
```bash
cd "C:\Users\Test\Desktop\code" && PYTHONUTF8=1 "/c/Users/Test/AppData/Local/Programs/Python/Python313/python.exe" monitor.py --baseline
```
期望：
- 控制台出现 `headless 未能通过 Cloudflare，将切到有头模式刷新 cookie`（headless 首访被拦）→ 有头自动通过 → `截图已保存 -> baseline/normal.png`；
- `baseline/normal.png` 文件存在，且内容为真实页面（非验证页）。
失败排查：若卡在验证页，多半是网络或该站改成交互式验证，先报错而不是继续。

- [ ] **Step 3: cookie 缓存后走 headless 快路径**

连跑两次单次巡检：
```bash
cd "C:\Users\Test\Desktop\code" && PYTHONUTF8=1 "/c/Users/Test/AppData/Local/Programs/Python/Python313/python.exe" monitor.py --once
cd "C:\Users\Test\Desktop\code" && PYTHONUTF8=1 "/c/Users/Test/AppData/Local/Programs/Python/Python313/python.exe" monitor.py --once
```
期望：两次都**不出现**"切到有头模式"，直接 `截图已保存 -> capture.png`；`monitor.log` 中 `[Layer 2] SSIM` 行显示正常评分。

- [ ] **Step 4: 验证日志无异常**

运行：
```bash
cd "C:\Users\Test\Desktop\code" && tail -30 monitor.log
```
期望：无 traceback；能看到关键词/SSIM 检测结果与"✅ 页面正常"或异常告警。

- [ ] **Step 5: checkpoint**

端到端跑通。若已 `git init`，把运行产物 `baseline/normal.png` 之外的源码提交（运行产物不入库）。

---

### Task 4: 更新文档（README_RUN.md + CLAUDE.md）

**Files:**
- Modify: `README_RUN.md`（一、四、六 节）
- Modify: `CLAUDE.md`（Cloudflare handling 一节）

- [ ] **Step 1: README_RUN.md 第一节替换**

把"## 一、它现在是怎么过 Cloudflare 的"整节（含下面的列表与引用块）替换为：

```markdown
## 一、它现在是怎么过 Cloudflare 的

实测结论（2026-08-12）：目标站是**托管型挑战**（managed challenge，非交互式）——
headless 首访必被 Cloudflare 拦下；而有头真实 Chromium 会自动通过，**不需要人工点击**。

因此采用两条腿：

1. **持久化浏览器配置目录**（`.browser_profile/`）会缓存 `cf_clearance` cookie。cookie 有效期内，CF 直接在后台放行，**无感 headless 截图**。
2. **cookie 过期被拦时**，自动切到有头模式刷新一次，但窗口会**移到屏幕外**（`--window-position=-32000,-32000`）：managed challenge 几秒内自动通过，全程无感、无需人工，cookie 刷新后窗口自动关闭。

> 旧的 `playwright-stealth` 已经移除——Patchright 本身已经打好补丁，再叠加 stealth 反而会留下可检测的痕迹。
```

- [ ] **Step 2: README_RUN.md 第四节更新开关**

把第四节代码块：

```python
CF_FORCE_HEADED   = False   # True = 始终有头（调试用）；False = 默认 headless，被拦才回退有头
CF_HEADLESS_TIMEOUT = 15    # headless 下等 CF 自动通过的秒数
CF_HEADED_TIMEOUT   = 120   # 有头模式下等 CF 通过的秒数（含你手动点击的时间）
```

替换为：

```python
CF_FORCE_HEADED     = False   # True = 始终有头（调试用）；False = 默认 headless，被拦才回退有头
CF_HEADED_OFFSCREEN = True    # True = 回退窗口移到屏幕外（默认，无感）；False = 可见窗口（调试用）
CF_HEADLESS_TIMEOUT = 15      # headless 下等 CF 自动通过的秒数
CF_HEADED_TIMEOUT   = 120     # 有头模式下等 CF 自动通过的秒数
```

并把该节末尾说明"调试时可以临时把 `CF_FORCE_HEADED = True`，这样每次都弹窗口，方便看 CF 到底卡在哪。"改为：

```markdown
调试时可以临时把 `CF_FORCE_HEADED = True` 且 `CF_HEADED_OFFSCREEN = False`，这样每次都弹出可见窗口，方便看 CF 到底卡在哪。
```

- [ ] **Step 3: README_RUN.md 第六节替换 FAQ Q1**

把：

```markdown
**Q: 弹出窗口后我点了验证框，但脚本还是报"未通过"？**
A: 默认有头模式最多等 120 秒（`CF_HEADED_TIMEOUT`）。如果你手慢了被超时截断，把 `CF_HEADED_TIMEOUT` 调大点（比如 300）。
```

替换为：

```markdown
**Q: 不是说永不弹窗吗，为什么后台好像有个浏览器进程？**
A: 那是回退路径的"屏幕外有头窗口"：窗口被移到屏幕外，你看不到也无需操作，托管型挑战几秒内自动通过后自动关闭。它不是可见弹窗，也不是需要你参与的验证。
```

- [ ] **Step 4: CLAUDE.md 更新 Cloudflare handling 一节**

把该节第二段（"Capture strategy (`_capture()`): headless first; ..."那条 bullet）以及开头的"Cloudflare-protected site"说明整体替换为：

```markdown
## Cloudflare handling (the non-obvious part)

`config.py` targets a Cloudflare-protected site, and `screenshot.py` handles the challenge via **Patchright** (a patched fork of Playwright — `import patchright`, never `playwright`) plus a **persistent browser profile**:

- Measured (2026-08-12): the site uses a **managed (non-interactive) challenge** — headless is always blocked on first visit, but headed real Chromium auto-passes in seconds with no clicking. Don't try to "beat" it with synthetic clicks.
- The persistent context at `.browser_profile/` caches the `cf_clearance` cookie, so after first pass the tool screenshots headless without being challenged.
- **Never delete `.browser_profile/`** — it resets the tool to "first visit" and forces a challenge on the next run.
- Capture strategy (`_capture()`): headless first; if the challenge isn't cleared within `CF_HEADLESS_TIMEOUT`, `_capture_once()` returns `None` and the caller retries headed **with the window moved off-screen** (`--window-position=-32000,-32000`, gated by `CF_HEADED_OFFSCREEN`) — the challenge auto-passes with zero interaction, then the cookie is cached. `CF_FORCE_HEADED = True` forces headed for debugging.
- Do **not** re-add `playwright-stealth` — Patchright is already patched, and stacking stealth leaves detectable traces.
```

- [ ] **Step 5: checkpoint**

文档更新完成。若已 `git init`，可提交：
```bash
git add README_RUN.md CLAUDE.md && git commit -m "docs: update Cloudflare flow to off-screen headed auto-pass"
```

---

## 验证清单（全部通过才算完成）

- [ ] Task 1: `import config` 成功，`CF_HEADED_OFFSCREEN is True`
- [ ] Task 2: `py_compile` 通过
- [ ] Task 3: `--baseline` 首访自动过验证生成 `baseline/normal.png`
- [ ] Task 3: `--once` 连跑两次走 headless 快路径、无回退日志
- [ ] Task 3: `monitor.log` 无 traceback
- [ ] Task 4: README_RUN.md / CLAUDE.md 已同步

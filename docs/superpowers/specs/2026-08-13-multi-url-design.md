# 多网址监控设计（Multi-URL Monitoring）

日期：2026-08-13
状态：已获用户确认，待实现

## 1. 背景与目标

当前工具只监控单个 URL（`config.TARGET_URL`），用户希望同时监控 **10 个以上
Downdetector 状态页**（如 amazon、netflix 等公司的 down 状态页）。所有站点：
- 同属一个域 `downdetector.com`，`cf_clearance` cookie 全站通用；
- 页面结构相同（Recharts 图表、`.recharts-wrapper`、tooltip 里的 `Reports: N`），
  图表选择器、报告数正则、关键词、SSIM 阈值全局共用；
- 仅「报告数阈值」和「基准图」需要按站区分。

告警方式：**每站独立告警**（消息带站名 + 该站自己的截图），同一轮多个站异常则
逐站各推一条。

## 2. 方案选择

选定**方案 A：每轮共享一个浏览器，串行访问所有站**。

理由：
- 同域共享 CF clearance，一个浏览器过一次 Cloudflare 即可放行全部站点；
- 单站耗时大头是 `networkidle` 超时（30s），降到 ~10s 后单站约 15s，
  10 站整轮约 3 分钟，完全装得下 10 分钟巡检间隔；
- 资源最省（约 1 个 Chromium 内存），实现简单，回归风险低。

放弃的方案：
- **方案 B（每站独立浏览器 + 线程池并发）**：需 3~4 个 Chromium（约 2GB 内存），
  每站首次各过一次 CF；隔离优势在本场景（同域、同结构）意义不大。
- **方案 C（异步 Playwright 页面级并发）**：需整体重写为 async，改动最大，
  10+ 站用不上；若未来站点增长到几十个再考虑。

## 3. 配置结构（config.py）

```python
# 监控目标列表：每站 name（文件名/消息/日志用）+ url + 该站报告数阈值
# reports_threshold 省略时用全局 REPORTS_THRESHOLD
TARGETS = [
    {"name": "amazon",  "url": "https://downdetector.com/status/amazon/",  "reports_threshold": 30},
    {"name": "netflix", "url": "https://downdetector.com/status/netflix/", "reports_threshold": 50},
]

# 兼容：TARGETS 为空时，仍可用原 TARGET_URL 配置单个站（旧用法不破坏）
TARGET_URL = "https://downdetector.com/status/amazon/"
```

- 新增 `NETWORKIDLE_TIMEOUT = 10`：单站渲染等待从 30s 降到 10s。
- 关键词、SSIM 阈值、图表选择器（`CHART_SELECTOR`）、报告正则（`REPORTS_PATTERN`）
  等保持全局；**不做**每站覆盖（YAGNI）。
- `BASELINE_IMAGE`、`REPORTS_THRESHOLD` 保留为全局默认值，作为 TARGETS 各站的
  兜底（兼容单站模式）。

## 4. 文件产物

| 项 | 路径 |
|---|---|
| 每站基准图 | `baseline/<name>.png`（如 `baseline/amazon.png`） |
| 每站最新截图 | `capture_<name>.png` |
| 持久化 profile | `.browser_profile/`（不变，同域共享 CF cookie） |

`--baseline` 一次性遍历所有站生成全部基准图。

## 5. 代码改动

### 5.1 screenshot.py

- 把现有 `_capture_once` 内「访问 → CF 处理 → 等渲染 → 悬停提取 → 隐藏 fixed
  浮层 → 截图 → 裁剪 → 取文本」抽成对单个 `page` 操作的函数（如
  `_capture_page(page, target_url, output_path, want_text)`），单次逻辑不变。
- 新增 `capture_all(targets) -> list[(截图路径, 页面文本)]`：
  - 打开**一个**持久化上下文（屏幕外有头窗口，同现有 CF_HEADED_OFFSCREEN 策略）；
  - **复用同一个 page**，按序 `goto` 每个站并调用 `_capture_page`；
  - 每站包 `try/except`：失败记 `ERROR`，继续下一站，整轮不中断；
  - 最后关闭上下文。
- 保留 `capture()` / `capture_with_text()` 单站入口（`--baseline` 及单站兼容用），
  底层改调 `_capture_page`。

### 5.2 detector.py

- `check_by_image(img_path, baseline_path=None)`：`baseline_path` 默认回落
  `config.BASELINE_IMAGE`。
- `check_by_reports(page_text, threshold=None)`：`threshold` 默认回落
  `config.REPORTS_THRESHOLD`。
- `detect(screenshot_path, page_text, baseline_path=None, reports_threshold=None)`：
  透传上面两个参数。默认值保持不变 → 单站模式行为完全不变。

### 5.3 monitor.py

- 目标解析：`TARGETS` 非空用之；为空则用 `TARGET_URL` 构造单站列表（name 从
  URL 取末段）。
- 每站输出路径辅助：`capture_<name>.png` / `baseline/<name>.png`。
- `make_baseline()`：遍历所有站，各生成基准图，日志逐站列出。
- `run_once()`：遍历各站 →
  - `capture_all` 得到每站截图与文本；
  - 逐站 `detect(..., baseline_path, reports_threshold)`；
  - 异常站逐站 `notify(该站截图, 原因, 站名)`；
  - 每站「Layer 结果 / 结论 / 是否推送」逐行打日志，单站失败不中断整轮。

### 5.4 notifier.py

- `notify(image_path, reason="", site_name="")`：`site_name` 非空时在消息中加上
  「站点: <name>」一行，其余格式不变。

## 6. 错误处理

- **单站隔离**：截图失败 / 检测报错 / CF 未通过，只对该站记 `ERROR`，其余站照常。
- **基准图缺失**：该站 Layer 2 自动跳过（现有行为）+ 日志提示先跑 `--baseline`。
- **CF 处理沿用现有逻辑**：屏幕外有头自动过托管型挑战 + Turnstile 点击兜底，
  首个站过完 CF 后其余站同域直连。

## 7. 验证方式

无测试框架，以实际运行验证：
1. `python monitor.py --baseline` —— 确认所有站基准图生成、日志逐站列出。
2. `python monitor.py --once` —— 确认所有站逐一巡检、日志按站汇总、
   异常站各自推送成功。

## 8. 明确不做（YAGNI）

- 不做每站关键词 / SSIM 阈值 / 图表选择器 / 报告正覆盖写；
- 不做页面级并发（方案 C）；
- 不做站点增删的热加载（改配置需重启）。

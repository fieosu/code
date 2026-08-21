# 运行说明

网页监控工具，已改为用 **Patchright**（打补丁的 Playwright）+ 持久化浏览器配置来绕过 Cloudflare。

---

## 一、它现在是怎么过 Cloudflare 的

实测结论（2026-08-12）：目标站是**托管型挑战**（managed challenge，非交互式）——
headless 任何变体都过不了（`cf_clearance` 与获取它的浏览器指纹绑定，headless 拿不到有头缓存的 cookie）；
而有头真实 Chromium 会自动通过，**不需要人工点击**。因此不"点验证框"，直接用屏幕外有头窗口：

1. **屏幕外有头窗口**（`--window-position=-32000,-32000`）：每次截图都用真实 Chromium，窗口被移到屏幕外，托管型挑战几秒内自动通过。全程无感、永不弹窗、无需人工。
2. **持久化浏览器配置目录**（`.browser_profile/`）缓存 `cf_clearance` cookie：cookie 有效期内 CF 直接在后台放行、加载更快；过期后自动重复上面的流程刷新 cookie，无感循环。

> 旧的 `playwright-stealth` 已经移除——Patchright 本身已经打好补丁，再叠加 stealth 反而会留下可检测的痕迹。

---

## 二、安装依赖（在你本机执行一次）

```powershell
# 1. 装 Python 依赖（已把 playwright 换成 patchright）
pip install -r requirements.txt

# 2. 装 Patchright 的浏览器内核（仅首次需要，会下载 Chromium）
patchright install chromium
```

或者直接跑：

```powershell
.\setup.bat
```

> 如果之前装过 playwright 的 chromium，可以不删，patchright 会再单独装一份到它自己的目录。

---

## 三、运行

```powershell
# 1. 编辑 config.py，填好 TARGETS（监控目标列表）和 Webhook key
notepad config.py

# 2. 开始监控（无需基准图——检测只用关键词 + 报告数阈值）
python monitor.py

# 调试：只跑一次就退出
python monitor.py --once
```

第一次访问目标站点时，`.browser_profile/` 是空的，CF 会拦一次：此时会自动启动一个**移到屏幕外**的 Chromium 窗口，托管型挑战几秒内自动通过。你看不到任何窗口、也不需要任何操作。

cookie 会缓存到 `.browser_profile/`，有效期内直接放行、加载更快；过期后自动重复上面的屏幕外有头流程，全程无感。

---

## 四、config.py 里和 Cloudflare 相关的开关

```python
CF_FORCE_HEADLESS = False    # True = 先试 headless（调试用）；False = 默认直接走屏幕外有头
CF_HEADED_OFFSCREEN = True   # True = 有头窗口移到屏幕外（默认，无感）；False = 可见窗口（调试用）
CF_HEADLESS_TIMEOUT = 15     # headless 下等 CF 自动通过的秒数（仅 CF_FORCE_HEADLESS=True 时用）
CF_HEADED_TIMEOUT   = 120    # 有头模式下等 CF 自动通过的秒数
```

调试时可以临时把 `CF_HEADED_OFFSCREEN = False`，这样每次都会弹出可见窗口，方便看 CF 到底卡在哪；想反过来试 headless，则把 `CF_FORCE_HEADLESS = True`。

---

## 五、目录产物

| 路径 | 作用 | 能不能删 |
|---|---|---|
| `.browser_profile/` | 缓存 cf_clearance / cookie / localStorage | **不要删**，删了等于回到第一次，必弹验证 |
| `capture_<name>.png` | 各站本轮截图 | 正常轮检测完即删；异常轮保留（即告警附件） |
| `monitor.log` | 运行日志 | 可删 |

---

## 六、常见问题

**Q: 不是说永不弹窗吗，为什么后台好像有个浏览器进程？**
A: 那是"屏幕外有头窗口"：窗口被移到屏幕外（`--window-position=-32000,-32000`），你看不到也无需操作，托管型挑战几秒内自动通过后自动关闭。它不是可见弹窗，也不是需要你参与的验证。

**Q: headless 从来没成功过？**
A: 正常。实测（2026-08-12）：本站 `cf_clearance` 与获取它的浏览器指纹绑定，headless 用不了有头缓存的 cookie，任何变体基本都过不了。所以默认直接走屏幕外有头；想亲自确认，可临时把 `CF_FORCE_HEADLESS = True` 跑一次对比。

**Q: `patchright` 是不是会覆盖我现有的 playwright？**
A: 不会。patchright 是独立的包，import 名是 `patchright`，和 `playwright` 共存不冲突。

**Q: 想完全后台、不弹任何窗口？**
A: 默认已经是——屏幕外有头窗口你看不到，也不会出现可见弹窗。想亲眼观察 CF 表现时，把 `CF_HEADED_OFFSCREEN` 改成 `False` 即可看到窗口。

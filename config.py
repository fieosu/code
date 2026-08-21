"""
网页监控配置
修改本文件即可适配你的场景，无需动其他代码。
"""

# ──────────────────────────────────────────────
# 监控目标
# ──────────────────────────────────────────────
# 多站模式：列表里每项是一个站点。
#   name: 站点名，用于文件名（capture_<name>.png）、消息与日志
#   url: 页面地址
#   reports_threshold: 该站报告数阈值（省略时用下方全局 REPORTS_THRESHOLD；0 = 关闭该层）
# 留空则回落单站模式（用下面的 TARGET_URL）。
TARGETS = [
    {"name": "rainbow-six", "url": "https://downdetector.com/status/rainbow-six/"},
    {"name": "escape-from-tarkov", "url": "https://downdetector.com/status/escape-from-tarkov/"},
    {"name": "battle-net", "url": "https://downdetector.com/status/battle-net/"},
    {"name": "roblox", "url": "https://downdetector.com/status/roblox/"},
    {"name": "world-of-warcraft", "url": "https://downdetector.com/status/world-of-warcraft/"},
    {"name": "hunt-showdown", "url": "https://downdetector.com/status/hunt-showdown/"},
    {"name": "battlefield", "url": "https://downdetector.com/status/battlefield/"},
    {"name": "league-of-legends", "url": "https://downdetector.com/status/league-of-legends/"},
    {"name": "war-thunder", "url": "https://downdetector.com/status/war-thunder/"},
    {"name": "ea", "url": "https://downdetector.com/status/ea/"},
    {"name": "dead-by-daylight", "url": "https://downdetector.com/status/dead-by-daylight/"},
    {"name": "valorant", "url": "https://downdetector.com/status/valorant/"},
    {"name": "apex-legends", "url": "https://downdetector.com/status/apex-legends/"},
    {"name": "pathofexile2", "url": "https://downdetector.com/status/pathofexile2/"},
    {"name": "path-of-exile", "url": "https://downdetector.com/status/path-of-exile/"},
    {"name": "dota-2", "url": "https://downdetector.com/status/dota-2/"},
    {"name": "gta5", "url": "https://downdetector.com/status/gta5/"},
    {"name": "helldivers-2", "url": "https://downdetector.com/status/helldivers-2/"},
    {"name": "warframe", "url": "https://downdetector.com/status/warframe/"},
    {"name": "elden-ring", "url": "https://downdetector.com/status/elden-ring/"},
    {"name": "destiny", "url": "https://downdetector.com/status/destiny/"},
]
TARGET_URL = "https://downdetector.com/status/rainbow-six/"           # 单站模式（TARGETS 为空时）的目标
# 检查间隔（秒）。多站（20 站）整轮约 6 分钟，建议 30 分钟：每轮跑 6 分钟 +
# 歇 24 分钟，页面压力与 CF 风险都低。报告数是累积的 24h 曲线、游戏故障通常
# 持续数小时，30 分钟发现不会漏点（不是秒级尖峰）。
CHECK_INTERVAL = 1800                         # 检查间隔（秒），默认 30 分钟

# ──────────────────────────────────────────────
# 静默时段（不巡检）
# ──────────────────────────────────────────────
# 每天 [QUIET_START, QUIET_END) 之间不巡检，其余时间照常。
# 24 小时制 "HH:MM"。两个值设成相同（如 "00:00"/"00:00"）即关闭静默。
# 也支持跨午夜区间（如 "22:00"~"06:00"）。
QUIET_START = "00:00"                        # 静默开始：午夜 24:00
QUIET_END = "09:00"                          # 静默结束：早上 09:00

# ──────────────────────────────────────────────
# 截图设置
# ──────────────────────────────────────────────
VIEWPORT_WIDTH = 1920                         # 视口宽度
VIEWPORT_HEIGHT = 1080                        # 视口高度
SCREENSHOT_FULL_PAGE = True                   # 是否截全页（False = 仅视口）
SCREENSHOT_CROP_TO_CHART = True               # 是否裁掉曲线图以下的内容（True = 只保留顶部到曲线图）
SCREENSHOT_CHART_BOTTOM_MARGIN = 40           # 曲线图底部额外保留的留白（像素），避免贴边

NETWORKIDLE_TIMEOUT = 10                      # 单站等待页面"网络空闲"的秒数。
                                              # 图表几秒就渲染好，10s 足够；
                                              # 降低它才能让多站整轮压缩到 ~3 分钟。

# ──────────────────────────────────────────────
# 异常检测 —— Layer 1：关键词匹配
# ──────────────────────────────────────────────
# 从页面提取文本后，命中任一关键词即判异常
ERROR_KEYWORDS = [
    "404", "500", "502", "503", "504",
    "error", "exception", "fatal", "crash",
    "not found", "forbidden", "unavailable",
    "错误", "异常", "无法访问", "找不到", "内部错误",
]

# ──────────────────────────────────────────────
# 异常检测 —— Layer 2：报告数（人数）
# ──────────────────────────────────────────────
# 目标站（Downdetector）的报告数藏在图表 tooltip 里，不悬停就不渲染。
# 截图流程会在加载后把图表滚入视口、悬停到"最新时刻"数据点，
# 让 tooltip（含 Reports: N）并入提取文本，再由本层解析判定。
# 把 CHART_SELECTOR 留空即可整体关闭该层。
CHART_SELECTOR = ".recharts-wrapper"          # 图表容器 CSS 选择器；空串 = 跳过悬停提取
                                          # 悬停定位为"扫描右缘几个位置、取 tooltip 时间最新者"，
                                          # 因为数据点位置随实时更新漂移，固定偏移不可靠。
REPORTS_PATTERN = r"Reports:\s*([\d,]+)"      # 从文本解析报告数的正则（取第 1 个捕获组）
REPORTS_THRESHOLD = 60                        # 报告数 ≥ 此值判异常；None 或 0 = 关闭 Layer 2
                                          # 多站全局兜底值，各站可在 TARGETS 里用 reports_threshold 单独覆盖

# ──────────────────────────────────────────────
# 飞书 / 钉钉 / 企业微信 —— Webhook 推送
# ──────────────────────────────────────────────
# 三选一，不需要的留空
WEBHOOK_TYPE = "wecom"                    # "feishu" / "dingtalk" / "wecom" / "generic"

# 企业微信（默认）
# 真实密钥放在被 .gitignore 忽略的 config_local.py 中（见文件末尾），勿直接写在这里。
WECOM_WEBHOOK_KEY = ""

# 钉钉
DINGTALK_WEBHOOK_URL = ""                  # 完整的 webhook URL（含 access_token）
DINGTALK_SECRET = ""                       # 加签密钥（如果开启了加签）

# 飞书
FEISHU_WEBHOOK_URL = ""                    # 自定义机器人的 webhook URL
FEISHU_SECRET = ""                         # 飞书加签密钥

# 通用 Webhook（直接 POST JSON）
GENERIC_WEBHOOK_URL = ""

# ──────────────────────────────────────────────
# 推送内容
# ──────────────────────────────────────────────
NOTIFY_MESSAGE = "⚠️ 网页异常告警\n\n检测到目标页面出现异常"

# ──────────────────────────────────────────────
# 重试 & 状态
# ──────────────────────────────────────────────
MAX_RETRIES = 3                             # 失败重试次数
RETRY_DELAY = 5                             # 重试间隔（秒）
LOG_FILE = "monitor.log"                    # 日志文件路径

# ──────────────────────────────────────────────
# Cloudflare 处理
# ──────────────────────────────────────────────
# 实测结论（2026-08-12）：目标站为托管型挑战（managed challenge，非交互式）——
# cf_clearance 与获取它的浏览器指纹绑定：headless 用不了有头缓存的 cookie，
# 对本类站基本必被拦；而有头真实 Chromium 自动通过、无需人工点击。
# 因此：默认直接走"屏幕外有头窗口"（自动通过、全程无感），headless 仅作调试用。
CF_FORCE_HEADLESS = False                   # True = 先试 headless（调试用）；默认直接走屏幕外有头
CF_HEADED_OFFSCREEN = True                  # True = 有头窗口移到屏幕外（默认，无感）；False = 可见窗口（调试用）
CF_HEADLESS_TIMEOUT = 15                    # headless 下等待 CF 自动通过的秒数（仅 CF_FORCE_HEADLESS=True 时用）
CF_HEADED_TIMEOUT = 120                     # 有头模式等待 CF 自动通过的秒数

# ──────────────────────────────────────────────
# 本地敏感配置覆盖（不入库）
# config_local.py 被 .gitignore 忽略，用于存放不该提交的密钥
# （企微 webhook key、钉钉/飞书加签密钥等）。文件不存在时用上面的默认值。
# 把密钥写在 config_local.py 里，不要写在本文件上方。
# ──────────────────────────────────────────────
try:
    from config_local import *          # noqa: F401,F403 —— 有则覆盖同名配置
except ImportError:
    pass

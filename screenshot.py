"""
网页截图模块

使用 Patchright（打补丁的 Playwright）截取目标网页，
通过持久化浏览器配置缓存 cf_clearance，自动通过 Cloudflare 人机验证。

工作原理：
  - 默认直接走"屏幕外有头窗口"（--window-position=-32000,-32000）：真实
    Chromium 自动通过托管型挑战，全程无感、永不弹窗、无需人工点击。
  - 持久化目录里的 cf_clearance cookie 有效期内，Cloudflare 直接放行、
    加载更快；过期后自动重复屏幕外有头流程刷新 cookie，无感循环。
  - headless 仅作调试路径（config.CF_FORCE_HEADLESS=True 时先试 headless，
    被拦再回退屏幕外有头）。

注意：不要再用 playwright-stealth，Patchright 已经打好补丁，叠加 stealth
反而会留下可被检测的痕迹。
"""

import os
import time
from datetime import datetime
from typing import Any, Iterator

from patchright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from PIL import Image

import config
from utils import logger

# 捕获结果：成功 (截图路径, 页面文本|None)；失败返回 None
# （CF 未通过/截图未写盘等失败一律返回 None 或抛异常，绝不返回"路径存在但内容不可信"的结果）
CaptureResult = tuple[str | None, str | None]

# 持久化浏览器配置目录：缓存 cf_clearance 等 cookie / localStorage
BROWSER_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".browser_profile"
)

# Cloudflare 挑战页特征（命中任一即视为处于验证页）
_CF_SELECTORS = [
    'iframe[title="Widget containing a Cloudflare security challenge"]',
    'iframe[src*="challenges.cloudflare.com"]',
    '#cf-challenge-running',
    '.cf-browser-verification',
    '#challenge-form',
    '#challenge-stage',
    'input[name="cf-turnstile-response"]',
    # 现代 CF 挑战页布局：URL 带 __cf_chl_rt_tk，正文为
    # "Performing security verification"（中文站为"正在进行安全验证"）
    'body:has-text("Performing security verification")',
    'body:has-text("正在进行安全验证")',
    'body:has-text("Just a moment")',
]


def _is_cloudflare_challenge(page: Any) -> bool:
    """当前是否处于 Cloudflare 验证页。"""
    try:
        url = page.url or ""
        if "challenges.cloudflare.com" in url or "__cf_chl" in url:
            return True
        title = page.title() or ""
        if "Just a moment" in title:
            return True
    except Exception:
        pass
    for sel in _CF_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def _wait_for_clearance(page: Any, timeout_s: int) -> bool:
    """轮询等待 Cloudflare 挑战消失。返回是否已通过。"""
    deadline = time.monotonic() + timeout_s
    stable = 0
    while time.monotonic() < deadline:
        if not _is_cloudflare_challenge(page):
            stable += 1
            if stable >= 2:      # 连续 2 次观察为非挑战，避免瞬时抖动误判
                return True
        else:
            stable = 0
        try:
            page.wait_for_timeout(1000)
        except Exception:
            break
    return not _is_cloudflare_challenge(page)


def _try_click_turnstile(page: Any) -> bool:
    """
    尝试在 Turnstile iframe 内点击 checkbox（跳过"先等自动通过"的干等）。

    挑战页刚加载时 iframe 可能还没渲染，这里最多等 5s 让 checkbox 出现再点。
    托管型挑战（managed challenge）没有可点的 checkbox：等满 5s 仍无元素即
    返回 False，页面自己几秒内自动通过，不受影响；交互式挑战则点一下即过。

    Returns:
        True = 实际点到了 checkbox；False = 无 checkbox 可点或点击失败
    """
    try:
        iframe = page.frame_locator(
            'iframe[title*="Cloudflare"], iframe[src*="challenges.cloudflare.com"]'
        )
        checkbox = iframe.locator(
            'input[type="checkbox"], [role="checkbox"], .cb-i, .mark'
        )
        checkbox.first.wait_for(state="attached", timeout=5000)
        checkbox.first.click(timeout=5000)
        logger.info("已在 Turnstile iframe 内点击 checkbox")
        return True
    except Exception as e:
        logger.debug(f"Turnstile 点击未生效（可忽略）: {e}")
    return False


def _tooltip_time(text: str):
    """
    解析 tooltip 首行的时刻标签（如 "Aug 13, 2026, 7:45 PM"）。

    Returns:
        datetime 或 None（文本为空或格式不符时）
    """
    if not text:
        return None
    first = text.strip().splitlines()[0].strip()
    try:
        return datetime.strptime(first, "%b %d, %Y, %I:%M %p")
    except ValueError:
        return None


def _hover_chart_for_tooltip(page: Any) -> float | None:
    """
    悬停图表，让 Recharts tooltip 渲染出报告数（人数）。

    目标站（Downdetector）的报告数不在静态文本里，只有鼠标悬停图表才会在
    tooltip 中显示 "Reports: N"。图表通常位于首屏下方，必须先滚入视口，
    否则鼠标坐标落在视口之外，hover 不生效。

    数据点是实时更新的、位置会随数据漂移，固定一个悬停点不可靠（实测可能
    落在相邻两点的吸附边界上而取到次新点）。因此从右缘向左扫几个位置，
    解析每个 tooltip 的时刻，取时间最新（即最右）的数据点，再悬停回该位置，
    让后续截图也带上最新 tooltip。
    通过 config.CHART_SELECTOR 控制是否启用，留空则跳过。

    Returns:
        曲线图底部的页面 Y 坐标（供裁剪截图用）；失败或未启用返回 None
    """
    sel = getattr(config, "CHART_SELECTOR", "") or ""
    if not sel:
        return None
    try:
        chart = page.locator(sel).first
        chart.scroll_into_view_if_needed(timeout=10000)
        box = chart.bounding_box()
        if not box:
            logger.debug("未获取到图表位置，跳过悬停提取")
            return None
        tip = page.locator(".recharts-tooltip-wrapper").first
        cy = box["y"] + box["height"] * 0.5
        best_dt, best_x = None, None
        # 从右缘(-1px)向左，步进 4px，扫 6 个位置，取时间最新者
        for i in range(6):
            x = box["x"] + box["width"] - 1 - i * 4
            page.mouse.move(x, cy, steps=3)          # 分步移动，确保触发 mousemove
            page.wait_for_timeout(400)               # 等 tooltip 更新
            try:
                t = tip.inner_text().strip()
            except Exception:
                t = ""
            dt = _tooltip_time(t)
            if dt and (best_dt is None or dt > best_dt):
                best_dt, best_x = dt, x
        if best_x is not None:
            page.mouse.move(best_x, cy, steps=3)     # 悬停回最新点，让截图带上 tooltip
            page.wait_for_timeout(300)
        # 曲线图底部的【文档】坐标，供裁剪用。注意 bounding_box() 返回的是视口
        # 坐标，滚动后不能直接当全页截图的像素行号用（否则会从图中间裁掉），
        # 这里用 JS 换算成文档坐标，与滚动无关。
        try:
            doc_bottom = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    return el.getBoundingClientRect().bottom + window.scrollY;
                }""",
                sel,
            )
            if doc_bottom:
                return float(doc_bottom)
        except Exception:
            pass
        return None
    except Exception as e:
        logger.debug(f"悬停图表获取 tooltip 失败（可忽略）: {e}")
        return None


def _crop_screenshot(output_path: str, chart_bottom) -> None:
    """
    按配置裁剪截图：只保留页面顶部到曲线图区域（含下方少量留白）。

    曲线图以下的说明、评论等冗长内容会被裁掉，图片更短、告警更聚焦。

    Args:
        output_path: 截图文件路径
        chart_bottom: 曲线图底部的页面 Y 坐标（文档坐标），None 时不裁剪
    """
    if not getattr(config, "SCREENSHOT_CROP_TO_CHART", False) or not chart_bottom:
        return
    margin = getattr(config, "SCREENSHOT_CHART_BOTTOM_MARGIN", 40)
    try:
        img = Image.open(output_path)
        width, height = img.size
        bottom = min(int(chart_bottom) + margin, height)
        if bottom < height:
            img.crop((0, 0, width, bottom)).save(output_path)
            logger.info(
                f"截图已裁剪 -> {output_path}（{width}×{bottom}px，原 {width}×{height}px）"
            )
    except Exception as e:
        logger.debug(f"截图裁剪失败（可忽略）: {e}")


def _hide_fixed_ui(page: Any) -> None:
    """
    隐藏页面上的 position:fixed 浮层（吸顶导航、悬浮按钮、底部广告等）。

    为什么必须隐藏：全页截图会把 fixed 元素画在当前滚动位置对应的文档坐标处。
    截图前为了渲染 tooltip 已把图表滚入视口（scrollY≈850），于是本应贴在视口
    顶部的导航栏会被画到文档中部，看起来就像被"截取到了中间"。隐藏后无论滚动到
    哪里，截图里都不会再出现飘在内容中间的浮层。

    注意：fixed 元素不占文档流，display:none 不会引起页面布局变化；图表和
    tooltip（position:absolute，在图表容器内）不受影响。sticky 元素按文档流
    绘制、不会错位，无需处理。
    """
    try:
        page.evaluate(
            """() => {
                for (const el of document.querySelectorAll('*')) {
                    if (getComputedStyle(el).position === 'fixed') {
                        el.style.display = 'none';
                    }
                }
            }"""
        )
        logger.info("已隐藏页面 fixed 浮层（吸顶导航/悬浮按钮/广告）")
    except Exception as e:
        logger.debug(f"隐藏 fixed 浮层失败（可忽略）: {e}")


def _capture_page(
    page: Any,
    url: str,
    output_path: str,
    *,
    headless: bool,
    want_text: bool,
) -> CaptureResult | None:
    """
    在已打开的 page 上完成单个目标截图全流程：访问 → CF → 等渲染 → 悬停提取
    tooltip → 隐藏 fixed 浮层 → 截图 → 裁剪 → 取文本。

    由单站入口 _capture_once 和多站入口 capture_all 共用：多站时同一浏览器
    上下文/页面串行调用本函数。

    Returns:
        (output_path, text|None)；失败返回 None——包括 headless 下被 CF 拦截需回退、
        有头模式下 CF 未通过、以及超时分支截图未写盘。文本拿不到时 text 为 None。
    """
    cf_headless_timeout = getattr(config, "CF_HEADLESS_TIMEOUT", 15)
    cf_headed_timeout = getattr(config, "CF_HEADED_TIMEOUT", 120)
    network_idle_timeout = getattr(config, "NETWORKIDLE_TIMEOUT", 10)

    try:
        logger.info(f"正在访问 {url} (headless={headless}) ...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        if _is_cloudflare_challenge(page):
            logger.warning("检测到 Cloudflare 验证")
            if headless:
                # headless 下只能等非交互式 managed challenge 自动放行；
                # 一旦是交互式挑战，headless 必然过不了，直接回退有头。
                passed = _wait_for_clearance(page, cf_headless_timeout)
                if not passed:
                    logger.warning("headless 未能通过 Cloudflare，将切到有头模式刷新 cookie")
                    return None
            else:
                # 有头模式：直接尝试点击 Turnstile checkbox，不再干等自动通过。
                # 托管型挑战（无 checkbox 可点）点击是空操作，页面自己几秒内
                # 自动通过；交互式挑战立刻点掉，省掉原来先干等 60s 的时间。
                # 之后统一等通过（最多 cf_headed_timeout 秒兜底）。
                _try_click_turnstile(page)
                passed = _wait_for_clearance(page, cf_headed_timeout)
                if not passed:
                    logger.error(
                        "有头模式仍未通过 Cloudflare（可能网络异常或该站为交互式验证）"
                    )
                    # CF 未通过意味着拿到的是挑战页而非真实页面：按失败处理。
                    # 不能把挑战页截图当正常结果返回——挑战页文本不含关键词会被
                    # 判"正常"并删图，站点就这样静默失守。
                    return None

        # 等待页面渲染完成（超时按当前状态继续）
        try:
            page.wait_for_load_state("networkidle", timeout=network_idle_timeout * 1000)
        except PlaywrightTimeout:
            logger.debug("networkidle 超时，按当前状态继续")
        page.wait_for_timeout(2000)
        try:
            logger.info(f"页面标题: {page.title()}")
        except Exception:
            pass

        # 悬停图表让 tooltip（含报告数）渲染——先悬停再截图，数字才会出现在图片里
        chart_bottom = _hover_chart_for_tooltip(page)

        # 隐藏 fixed 浮层，避免吸顶导航被全页截图画到文档中部（见 _hide_fixed_ui）
        _hide_fixed_ui(page)

        page.screenshot(path=output_path, full_page=config.SCREENSHOT_FULL_PAGE)
        _crop_screenshot(output_path, chart_bottom)
        text = page.inner_text("body") if want_text else None
        logger.info(f"截图已保存 -> {output_path}")
        return output_path, text

    except PlaywrightTimeout:
        logger.warning("页面加载超时，尝试当前状态截图")
        chart_bottom = _hover_chart_for_tooltip(page)
        _hide_fixed_ui(page)
        try:
            page.screenshot(path=output_path, full_page=config.SCREENSHOT_FULL_PAGE)
            _crop_screenshot(output_path, chart_bottom)
        except Exception as e:
            # 截图失败不能仍返回路径：调用方会拿一个不存在的文件去推送，
            # notify 读图时才 FileNotFoundError，告警丢失且重试白烧时间
            logger.error(f"页面加载超时且截图失败: {e}")
            return None
        text = None
        if want_text:
            try:
                text = page.inner_text("body")
            except Exception:
                text = None
        return output_path, text


def _capture_once(
    url: str,
    output_path: str,
    *,
    headless: bool,
    want_text: bool,
) -> CaptureResult | None:
    """
    单站：启动一个持久化浏览器上下文完成一次截图。

    返回 (path, text|None)；若因 Cloudflare 拦截需要回退有头模式，返回 None。
    """
    with sync_playwright() as p:
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
        page = context.pages[0] if context.pages else context.new_page()
        try:
            return _capture_page(page, url, output_path, headless=headless, want_text=want_text)
        finally:
            context.close()


def _capture(url: str, output_path: str, *, want_text: bool) -> CaptureResult:
    """默认直接走屏幕外有头（真实 Chromium 自动过托管型挑战，全程无感）。

    实测（2026-08-12）：cf_clearance 与获取它的浏览器指纹绑定，headless
    用不了有头缓存的 cookie，对本类站基本必被拦；headless 仅作调试用。
    """
    if getattr(config, "CF_FORCE_HEADLESS", False):
        result = _capture_once(url, output_path, headless=True, want_text=want_text)
        if result is not None:
            return result
        logger.info("headless 未通过 Cloudflare，回退屏幕外有头")
    result = _capture_once(url, output_path, headless=False, want_text=want_text)
    if result is None:
        # 显式抛错而非 assert（assert 在 python -O 下会被剥掉）：失败要大声，
        # 不能让 None 以"成功类型"流进 capture()/capture_with_text()
        raise RuntimeError("有头模式未能通过 Cloudflare，单站捕获失败")
    return result


def capture(url: str | None = None, output_path: str = "capture.png") -> str:
    """
    截取指定网页。

    Args:
        url: 网页地址，默认取 config.TARGET_URL
        output_path: 截图保存路径

    Returns:
        截图文件的保存路径；失败（如 CF 未通过）抛异常
    """
    target = url or config.TARGET_URL
    path, _ = _capture(target, output_path, want_text=False)
    return path


def capture_with_text(
    url: str | None = None, output_path: str = "capture.png"
) -> tuple[str, str]:
    """
    截取网页并同时提取页面文本（供 Layer 1 检测用）。

    Returns:
        (截图路径, 页面可见文本)；失败（如 CF 未通过）抛异常
    """
    target = url or config.TARGET_URL
    path, text = _capture(target, output_path, want_text=True)
    return path, (text or "")


def iter_captures(targets: list[config.Target]) -> Iterator[tuple[config.Target, str | None, str]]:
    """
    多站：启动一个持久化浏览器上下文，串行捕获目标，每站完成立即产出。

    与 capture_all 攒齐全部结果再返回不同：本函数每截完一个站就 yield 一次，
    调用方随即检测——某站一命中阈值立刻推送告警，不用等整轮全部站点截完
    （每站约 20s，整轮时长随站点数线性增长）。
    所有站仍共用同一个浏览器上下文：同域（downdetector.com）共享同一份
    cf_clearance，CF 只过一次。每站失败只记日志并继续，整轮不中断。
    固定走屏幕外有头窗口（生产路径，与默认单站策略一致）。

    Args:
        targets: 监控目标列表（结构见 config.Target）

    Yields:
        逐个产出 (target, output_path|None, page_text|"")，失败项 output_path 为 None
    """
    if not targets:
        return

    with sync_playwright() as p:
        launch_args = ["--no-sandbox"]
        if getattr(config, "CF_HEADED_OFFSCREEN", True):
            launch_args.append("--window-position=-32000,-32000")
        context = p.chromium.launch_persistent_context(
            user_data_dir=BROWSER_PROFILE_DIR,
            headless=False,
            viewport={
                "width": config.VIEWPORT_WIDTH,
                "height": config.VIEWPORT_HEIGHT,
            },
            args=launch_args,
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            for t in targets:
                name = t.get("name") or "site"
                output_path = f"capture_{name}.png"
                try:
                    result = _capture_page(page, t["url"], output_path, headless=False, want_text=True)
                    if result is None:
                        logger.error(f"[{name}] 捕获未返回结果，跳过检测")
                        yield (t, None, "")
                        continue
                    yield (t, result[0], result[1] or "")
                except Exception as e:
                    logger.error(f"[{name}] 捕获失败: {e}")
                    yield (t, None, "")
        finally:
            context.close()


def capture_all(targets: list[config.Target]) -> list[tuple[str, str | None, str]]:
    """
    多站：串行捕获所有目标，攒齐后一次性返回（兼容接口）。

    注意：它把整轮截图攒齐才返回，某站异常要等所有站截完才检测。
    需要"每站一完成立即检测、命中阈值立刻告警"时请用 iter_captures。
    """
    return [
        (t.get("name") or "site", path, text)
        for t, path, text in iter_captures(targets)
    ]

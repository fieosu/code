"""
网页监控主程序

流程:
    1. 截图目标网页 + 提取页面文本
    2. 阈值检测（关键词 + 报告数）
    3. 异常则推送截图到 Webhook（正常轮不留截图）
    4. 按配置间隔循环执行

使用:
    python monitor.py              # 正常模式（每 CHECK_INTERVAL 秒巡检）
    python monitor.py --once       # 单次运行（调试用）
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from time import sleep
from urllib.parse import urlparse

import config
from detector import detect
from notifier import notify, webhook_configured
from screenshot import iter_captures
from utils import logger


def validate_url(url: str) -> bool:
    """检查 URL 格式是否合法。"""
    result = urlparse(url)
    return all([result.scheme, result.netloc])


def get_targets() -> list[config.Target]:
    """
    解析监控目标列表。

    优先用 config.TARGETS；为空时回落单站模式，用 config.TARGET_URL，
    name 取 URL 路径最后一段（如 /status/amazon/ → amazon）。
    """
    if config.TARGETS:
        return list(config.TARGETS)
    url = config.TARGET_URL
    segs = [s for s in urlparse(url).path.split("/") if s]
    name = segs[-1] if segs else "site"
    return [config.Target(name=name, url=url)]


def run_once(cycle: int | None = None) -> tuple[bool, bool]:
    """
    执行单次检测循环：逐个站「捕获 → 阈值检测 → 异常立即告警」，
    某站一命中阈值马上推送，不攒到整轮截完才处理。

    单站隔离：任一站的检测/推送/删除出错只记日志并继续，不拖垮本轮其余站点。
    轮内推送熔断：某站推送失败后，本轮后续站点不再重试推送（只保留截图记日志），
    避免每站白烧整段重试时间；下一轮开始自动复位。

    Args:
        cycle: 第几轮巡检（持续模式递增；单次运行可不传）

    Returns:
        (是否有任一站点异常, 是否有推送失败)
    """
    label = f"巡检 #{cycle}" if cycle is not None else "巡检"
    start = time.monotonic()
    logger.info("=" * 50)
    logger.info(f"{label} 开始")

    targets = get_targets()
    logger.info(f"本轮共 {len(targets)} 个站点")

    # 逐站：捕获 → 检测 → 命中立即告警，不等整轮截完（避免积攒延误）
    abnormal_count = 0
    push_failed = False
    webhook_healthy = True
    for t, path, text in iter_captures(targets):
        name = t.get("name") or "site"
        logger.info(f"── 站点: {name} ({t.get('url', '')}) ──")
        # 单站隔离：本站任一环节出错只记日志继续，不中断本轮其余站点的检测
        try:
            if not path or not os.path.exists(path):
                # 路径不存在（截图没写盘）与捕获失败同样处理，防止推送时才 FileNotFoundError
                logger.error(f"[{name}] 截图失败，跳过检测")
                continue
            if not text:
                # 文本拿不到 ≠ 正常：无法检测，按可疑告警并保留截图，绝不删证据
                abnormal, reason = True, "页面文本为空，无法完成检测（页面可能加载异常或被拦截）"
            else:
                threshold = t.get("reports_threshold")     # None → detect 内回落全局
                abnormal, reason = detect(text, reports_threshold=threshold)
            if not abnormal:
                # 正常轮不留截图：截图只在异常时作为告警附件保留
                try:
                    os.remove(path)
                    logger.info(f"[{name}] 页面正常，删除本轮截图")
                except OSError:
                    pass
                continue

            abnormal_count += 1
            if not webhook_healthy:
                push_failed = True
                logger.error(f"[{name}] 检测异常，但本轮推送已熔断（此前推送失败），仅保留截图 {path}")
                continue
            try:
                notify(path, reason, site_name=name)   # 一命中阈值就推，不积攒
            except Exception as e:
                # 推送失败：置熔断标志，避免本轮剩余站点各白烧一整段重试时间
                push_failed = True
                webhook_healthy = False
                logger.error(f"[{name}] 告警推送失败: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"[{name}] 本站处理出错: {e}", exc_info=True)

    elapsed = time.monotonic() - start
    logger.info(
        f"{label} 结束 — 异常站点 {abnormal_count}/{len(targets)}，耗时 {elapsed:.1f}s"
    )
    if push_failed:
        logger.warning("本轮存在推送失败，告警可能未送达，请检查 webhook 配置与网络")
    logger.info("=" * 50)
    return abnormal_count > 0, push_failed


def _parse_hhmm(value: object, key: str = "") -> int:
    """
    把 "HH:MM" 解析成当天分钟数（如 "09:00" → 540）。"24:00" 归一化为 1440（午夜）。

    解析失败抛 ValueError（带配置键名），供启动校验给出明确提示；
    也挡住 config.py 注释里"午夜 24:00"这类合法写法在 now.replace(hour=24)
    处炸出 ValueError 的老问题。
    """
    key = key or "时间"
    if not isinstance(value, str):
        raise ValueError(f"{key} 配置须为 \"HH:MM\" 字符串，实际为: {value!r}")
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"{key} 配置格式应为 \"HH:MM\"，实际为: {value!r}")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 24 and 0 <= m <= 59) or (h == 24 and m != 0):
        raise ValueError(f"{key} 配置超出 00:00~24:00 范围: {value!r}")
    return h * 60 + m


def _validate_quiet_config() -> None:
    """启动时校验静默时段配置，非法直接退出（避免运行中在 try 外炸掉整个进程）。"""
    try:
        _parse_hhmm(getattr(config, "QUIET_START", "00:00"), "QUIET_START")
        _parse_hhmm(getattr(config, "QUIET_END", "00:00"), "QUIET_END")
    except ValueError as e:
        logger.error(f"{e}，请检查 config.py")
        sys.exit(1)


def _in_quiet_window(now: datetime | None = None) -> bool:
    """
    当前是否处于静默时段（每天 [QUIET_START, QUIET_END) 之间不巡检）。

    两个配置值相同 = 关闭静默。支持跨午夜区间（如 "22:00"~"06:00"）。
    """
    start = _parse_hhmm(getattr(config, "QUIET_START", "00:00"), "QUIET_START")
    end = _parse_hhmm(getattr(config, "QUIET_END", "00:00"), "QUIET_END")
    if start == end:
        return False
    now = now or datetime.now().astimezone()
    cur = now.hour * 60 + now.minute
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def _seconds_until_quiet_end() -> float:
    """距静默时段结束还有多少秒；若结束时刻已过则到明天同一时刻。"""
    end = _parse_hhmm(getattr(config, "QUIET_END", "00:00"), "QUIET_END")
    now = datetime.now().astimezone()
    # "24:00"（1440）归一化为次日 00:00，避免 now.replace(hour=24) 的 ValueError
    target = now.replace(hour=(end // 60) % 24, minute=end % 60, second=0, microsecond=0)
    if end >= 24 * 60 or target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _validate_targets(targets: list[config.Target]) -> None:
    """
    启动时逐条校验监控目标：必含 name/url、URL 合法、threshold 为非负整数。
    config.Target 只是类型注解、无运行时校验，坏条目（如缺 url、threshold 写成
    字符串）不在这里拦住，就会以裸 KeyError/TypeError 的形式在检测中途炸掉整轮。
    """
    for t in targets:
        name = t.get("name") or ""
        if not name or not t.get("url"):
            logger.error(f"TARGETS 条目缺少 name/url 字段: {t}，请检查 config.py")
            sys.exit(1)
        if not validate_url(t["url"]):
            logger.error(f"URL 不合法: {t['url']}（目标 {name}），请检查 config.py")
            sys.exit(1)
        th = t.get("reports_threshold")
        if th is not None and (isinstance(th, bool) or not isinstance(th, int) or th < 0):
            logger.error(
                f"站点 {name} 的 reports_threshold 须为非负整数，实际为: {th!r}，请检查 config.py"
            )
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="网页监控 — 定时截图 + 阈值检测 + Webhook 告警")
    parser.add_argument("--once", action="store_true", help="仅运行一次后退出")
    args = parser.parse_args()

    # 启动校验：配置问题在这里带明确提示失败，绝不带病进入巡检循环
    targets = get_targets()
    _validate_targets(targets)
    if not webhook_configured():
        logger.error(
            f"不支持的 Webhook 类型: {config.WEBHOOK_TYPE}（目前仅支持 wecom），"
            f"请检查 config.py / config_local.py"
        )
        sys.exit(1)
    _validate_quiet_config()

    # 模式：单次运行
    if args.once:
        _, push_failed = run_once()
        if push_failed:
            sys.exit(2)     # 推送失败以非零码退出，让外层计划任务/CI 能感知告警未送达
        return

    # 模式：持续巡检
    logger.info(
        f"开始持续监控\n"
        f"  目标数: {len(targets)}\n"
        f"  间隔: {config.CHECK_INTERVAL}s ({config.CHECK_INTERVAL // 60} 分钟)\n"
        f"  静默时段: {config.QUIET_START}~{config.QUIET_END}（期间不巡检）\n"
        f"  Webhook: {config.WEBHOOK_TYPE}\n"
        f"  告警条件: 关键词命中 / 报告数 ≥ {config.REPORTS_THRESHOLD}\n"
    )

    cycle = 0
    try:
        while True:
            # 静默时段：跳过巡检，直接睡到恢复时刻再继续（避免空转）
            if _in_quiet_window():
                wait = _seconds_until_quiet_end()
                logger.info(
                    f"处于静默时段（{config.QUIET_START}~{config.QUIET_END}），"
                    f"跳过本轮巡检，{wait / 3600:.1f} 小时后恢复"
                )
                sleep(wait)
                continue

            try:
                cycle += 1
                _, push_failed = run_once(cycle=cycle)
                if push_failed:
                    logger.warning("上轮存在推送失败，若持续出现请检查 webhook")
            except Exception as e:
                logger.error(f"巡检出错: {e}", exc_info=True)

            logger.info(
                f"下次巡检: {config.CHECK_INTERVAL // 60} 分钟（{config.CHECK_INTERVAL}s）后\n"
            )
            sleep(config.CHECK_INTERVAL)
    except KeyboardInterrupt:
        # 包住整个循环：Ctrl+C 多数时候落在两处 sleep 里，只包 run_once 会裸 traceback 退出
        logger.info("用户中断，退出")


if __name__ == "__main__":
    main()

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
from datetime import datetime, timedelta
from time import sleep
from urllib.parse import urlparse

import config
from detector import detect
from notifier import notify
from screenshot import iter_captures
from utils import logger


def validate_url(url: str) -> bool:
    """检查 URL 格式是否合法。"""
    result = urlparse(url)
    return all([result.scheme, result.netloc])


def get_targets() -> list:
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
    return [{"name": name, "url": url}]


def run_once(cycle: int = None) -> bool:
    """
    执行单次检测循环：逐个站「捕获 → 阈值检测 → 异常立即告警」，
    某站一命中阈值马上推送，不攒到整轮截完才处理。

    Args:
        cycle: 第几轮巡检（持续模式递增；单次运行可不传）

    Returns:
        是否有任一站点异常
    """
    label = f"巡检 #{cycle}" if cycle else "巡检"
    start = datetime.now()
    logger.info("=" * 50)
    logger.info(f"{label} 开始 — {start.strftime('%Y-%m-%d %H:%M:%S')}")

    targets = get_targets()
    logger.info(f"本轮共 {len(targets)} 个站点")

    # 逐站：捕获 → 检测 → 命中立即告警，不等整轮截完（避免积攒延误）
    abnormal_count = 0
    for t, path, text in iter_captures(targets):
        name = t.get("name") or "site"
        logger.info(f"── 站点: {name} ({t['url']}) ──")
        if not path:
            logger.error(f"[{name}] 截图失败，跳过检测")
            continue
        threshold = t.get("reports_threshold")     # None → detect 内回落全局
        abnormal, reason = detect(text, reports_threshold=threshold)
        if abnormal:
            abnormal_count += 1
            notify(path, reason, site_name=name)   # 一命中阈值就推，不积攒
        else:
            # 正常轮不留截图：截图只在异常时作为告警附件保留
            try:
                os.remove(path)
                logger.info(f"[{name}] 页面正常，删除本轮截图")
            except OSError:
                pass

    elapsed = (datetime.now() - start).total_seconds()
    logger.info(
        f"{label} 结束 — 异常站点 {abnormal_count}/{len(targets)}，耗时 {elapsed:.1f}s"
    )
    logger.info("=" * 50)
    return abnormal_count > 0


def _parse_hhmm(value: str) -> int:
    """把 "HH:MM" 解析成当天分钟数（如 "09:00" → 540）。"""
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def _in_quiet_window(now: datetime = None) -> bool:
    """
    当前是否处于静默时段（每天 [QUIET_START, QUIET_END) 之间不巡检）。

    两个配置值相同 = 关闭静默。支持跨午夜区间（如 "22:00"~"06:00"）。
    """
    start = _parse_hhmm(getattr(config, "QUIET_START", "00:00"))
    end = _parse_hhmm(getattr(config, "QUIET_END", "00:00"))
    if start == end:
        return False
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def _seconds_until_quiet_end() -> float:
    """距静默时段结束还有多少秒；若结束时刻已过则到明天同一时刻。"""
    end = _parse_hhmm(getattr(config, "QUIET_END", "00:00"))
    now = datetime.now()
    target = now.replace(hour=end // 60, minute=end % 60, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def main():
    parser = argparse.ArgumentParser(description="网页监控 — 定时截图 + 阈值检测 + Webhook 告警")
    parser.add_argument("--once", action="store_true", help="仅运行一次后退出")
    args = parser.parse_args()

    # 校验目标 URL
    for t in get_targets():
        if not validate_url(t["url"]):
            logger.error(f"URL 不合法: {t['url']}（目标 {t['name']}），请检查 config.py")
            sys.exit(1)

    # 模式：单次运行
    if args.once:
        run_once()
        return

    # 模式：持续巡检
    logger.info(
        f"开始持续监控\n"
        f"  目标数: {len(get_targets())}\n"
        f"  间隔: {config.CHECK_INTERVAL}s ({config.CHECK_INTERVAL // 60} 分钟)\n"
        f"  静默时段: {config.QUIET_START}~{config.QUIET_END}（期间不巡检）\n"
        f"  Webhook: {config.WEBHOOK_TYPE}\n"
        f"  告警条件: 关键词命中 / 报告数 ≥ {config.REPORTS_THRESHOLD}\n"
    )

    cycle = 0
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
            run_once(cycle=cycle)
        except KeyboardInterrupt:
            logger.info("用户中断，退出")
            break
        except Exception as e:
            logger.error(f"巡检出错: {e}", exc_info=True)

        logger.info(
            f"下次巡检: {config.CHECK_INTERVAL // 60} 分钟（{config.CHECK_INTERVAL}s）后\n"
        )
        sleep(config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()

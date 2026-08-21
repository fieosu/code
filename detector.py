"""
异常检测模块 —— 阈值检测

Layer 1 : 页面文本关键词匹配（快速、精准）
Layer 2 : 报告数（人数）超过阈值（来自图表 tooltip）

任一图层判定异常即返回异常。已按需求移除图像 SSIM 对比（不再需要基准图）。
"""

import re
from symtable import SymbolTable

import config
from utils import logger


def check_by_keywords(text: str) -> tuple[bool, str]:
    """
    Layer 1：从页面文本中匹配错误关键词。

    Returns:
        (是否异常, 命中原因)
    """
    text_lower = text.lower()
    for kw in config.ERROR_KEYWORDS:
        if kw.lower() in text_lower:
            logger.info(f"[Layer 1] 命中关键词 → \"{kw}\"")
            return True, f"关键词命中: {kw}"
    return False, ""


def check_by_reports(page_text: str, threshold: int | None = None) -> tuple[bool, str]:
    """
    Layer 2：解析报告数（人数），超过阈值判异常。

    报告数来自图表 tooltip，由 screenshot 悬停图表后并入提取文本。
    未配置（REPORTS_PATTERN 为空或阈值非正）时静默跳过。

    Args:
        page_text: 页面文本
        threshold: 报告数阈值；None 时用 config.REPORTS_THRESHOLD。
                   非正数 = 关闭本层。

    Returns:
        (是否异常, 异常原因)
    """
    pattern = getattr(config, "REPORTS_PATTERN", "") or ""
    if threshold is None:
        # 用 int() 强制收敛，避免基于用法的分析把 config 的可空值（REPORTS_THRESHOLD 可为 None）
        # 保留成 int | None，导致后续 "<= 0" 报 optional operand 错误。
        threshold = int(getattr(config, "REPORTS_THRESHOLD", 0) or 0)
    if not pattern or threshold <= 0:
        return False, ""

    m = re.search(pattern, page_text, re.IGNORECASE)
    if not m:
        logger.info("[Layer 2] 文本中未找到报告数，跳过")
        return False, ""
    if m.lastindex is None:
        logger.warning("[Layer 2] REPORTS_PATTERN 未包含捕获组，跳过")
        return False, ""

    try:
        count = int(m.group(1).replace(",", ""))
    except ValueError:
        logger.warning(f"[Layer 2] 报告数字段无法解析: {m.group(1)!r}，跳过")
        return False, ""
    logger.info(f"[Layer 2] 当前报告数 = {count}（阈值 {threshold}）")

    if count >= threshold:
        return True, f"报告数异常: {count}（≥ 阈值 {threshold}）"
    return False, ""


def detect(page_text: str, reports_threshold: int | None = None) -> tuple[bool, str]:
    """
    综合检测入口（纯阈值，无图像对比）。

    Args:
        page_text: 页面可见文本
        reports_threshold: 该站报告数阈值；None 用 config.REPORTS_THRESHOLD

    Returns:
        (是否异常, 异常原因)
    """
    results = []

    # Layer 1
    abnormal, reason = check_by_keywords(page_text)
    results.append(("Layer 1 关键词", abnormal, reason))

    # Layer 2
    abnormal, reason = check_by_reports(page_text, reports_threshold)
    results.append(("Layer 2 报告数", abnormal, reason))

    # 汇总各层结果（保留 Layer 1→2 的优先级，取最先命中的原因）
    logger.info("检测结果:")
    for name, is_abnormal, reason in results:
        if is_abnormal:
            logger.info(f"  {name}: ❌ {reason}")
        else:
            logger.info(f"  {name}: ✅ 正常")

    # 按 Layer 顺序取最先命中的原因，避免后一层覆盖先命中的
    hit_reason = next((reason for _, is_abnormal, reason in results if is_abnormal), None)

    if hit_reason:
        logger.warning(f"🚨 异常检测到: {hit_reason}")
        return True, hit_reason

    logger.info("✅ 页面正常，未检出异常")
    return False, ""

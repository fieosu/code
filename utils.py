"""
通用工具：日志、重试装饰器
"""

import logging
import sys
from functools import wraps
from time import sleep

import config

# ──────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────
logger = logging.getLogger("monitor")
logger.setLevel(logging.DEBUG)

fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

# 控制台输出
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(fmt)
logger.addHandler(sh)

# 文件输出
if config.LOG_FILE:
    fh = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)


def retry(max_retries: int = None, delay: int = None):
    """
    重试装饰器。

    用法:
        @retry()
        def flaky_func():
            ...
    """
    _max = max_retries if max_retries is not None else config.MAX_RETRIES
    _delay = delay if delay is not None else config.RETRY_DELAY

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_err = None
            for attempt in range(1, _max + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_err = e
                    logger.warning(f"{func.__name__} 第 {attempt}/{_max} 次失败: {e}")
                    if attempt < _max:
                        sleep(_delay)
            raise last_err
        return wrapper
    return decorator

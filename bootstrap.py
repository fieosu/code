"""
打包(frozen)环境路径引导 —— 必须在任何 import config 之前导入。

为什么需要它（PyInstaller onefile 的两个坑）：
  1. onefile 每次运行都把资源解包到临时目录（sys._MEIPASS），__file__ 指向那里。
     运行时产物（.browser_profile/、monitor.log、capture_*.png）若按 __file__
     或 CWD 定位，每次都会落到新临时目录 / 随机工作目录——CF cookie 缓存直接
     失效（等于每次都是"首次访问"，必被 Cloudflare 拦）。因此冻结环境下所有
     产物一律锚定到 exe 所在目录。
  2. config.py / config_local.py 被排除在 exe 外（改配置无需重新打包），冻结后
     sys.path 默认不含 exe 目录，import config 会找不到——启动时把 exe 目录
     插到 sys.path 最前即可读到外置配置。

用法：monitor.py 第一行 import bootstrap（先于一切业务 import）；
产物路径一律用 app_path() 拼接。
"""

import os
import sys

if getattr(sys, "frozen", False):
    # PyInstaller 打包后：exe 所在目录是状态与配置的唯一根
    # （onefile 的 sys._MEIPASS 是临时解包目录，每次运行都变，绝不能用于存放状态）
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    # 外置配置：exe 目录插到 sys.path 最前，import config / config_local 读 exe 旁的文件
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    # 随包浏览器：exe 旁 browsers/（发布包从构建机拷贝的 Chromium 内核）。
    # 必须在 patchright 启动浏览器之前设置；用户已自定义 PLAYWRIGHT_BROWSERS_PATH
    # 时尊重之。两边都没有则给出明确提示（全新机器上默认路径必无浏览器）。
    _browsers = os.path.join(APP_DIR, "browsers")
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        if os.path.isdir(_browsers):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _browsers
        else:
            # 构建机/开发者本机通常装有默认浏览器目录，此时只是提示、放行回退；
            # 都没有则必然启动失败，直接报清楚原因并退出，不留谜语
            _defaults = [
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright-patchright"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
            ]
            if not any(os.path.isdir(d) for d in _defaults):
                sys.stderr.write(
                    f"[bootstrap] 未找到随包浏览器目录: {_browsers}\n"
                    f"[bootstrap] 发布包必须包含 browsers/（Chromium 内核）。"
                    f"请重新运行 build_exe.bat 完整打包后再部署。\n"
                )
                sys.exit(1)
            sys.stderr.write(
                "[bootstrap] 提示: 未找到 exe 旁的 browsers/，回退本机默认浏览器目录\n"
            )
else:
    # 源码运行：锚定到脚本目录（与原 __file__ 行为一致）
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def app_path(*parts: str) -> str:
    """把相对产物路径锚定到应用目录（源码跑=脚本目录；exe 跑=exe 所在目录）。

    传入已是绝对路径时原样返回（os.path.join 遇到带盘符的绝对路径会丢弃前段），
    因此调用方无需自己判断相对/绝对。
    """
    return os.path.join(APP_DIR, *parts)

@echo off
chcp 65001 >nul
echo ========================================
echo   网页监控工具 - 初始化
echo ========================================
echo.

echo [1/2] 安装 Python 依赖...
pip install -r requirements.txt

echo.
echo [2/2] 安装 Patchright 浏览器（仅首次需要）...
patchright install chromium

echo.
echo ========================================
echo   初始化完成！
echo.
echo   下一步:
echo   1. 编辑 config.py，填入你的目标 URL 和 Webhook key
echo   2. 运行: python monitor.py              （开始监控）
echo      调试: python monitor.py --once       （只跑一次）
echo ========================================
pause

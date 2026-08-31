@echo off
setlocal
chcp 65001 >nul
rem ============================================================
rem 打包 monitor.exe（onefile）—— 产出 dist\ 即发布目录：
rem   dist\monitor.exe      单文件主程序（含 Python 运行时 + patchright driver）
rem   dist\config.py        外置配置模板（改配置无需重新打包）
rem   dist\browsers\        随包 Chromium 内核（全新机器无需装 Python/浏览器）
rem 部署：把 dist\ 整个目录拷到目标机；config_local.py（密钥）在目标机自行创建。
rem ============================================================
cd /d "%~dp0"

rem 1) PyInstaller 打包。config / config_local 排除在包外：
rem    运行时由 bootstrap.py 把 exe 目录插到 sys.path 最前，读 exe 旁的外置配置。
pyinstaller --noconfirm --clean --onefile --console --name monitor ^
  --collect-all patchright ^
  --exclude-module config --exclude-module config_local ^
  monitor.py
if errorlevel 1 (
  echo [build] PyInstaller 打包失败，请检查上方报错
  exit /b 1
)

rem 2) 布置发布目录：外置配置模板
copy /y config.py dist\config.py >nul

rem 3) 拷贝随包浏览器（patchright 装在 ms-playwright-patchright；兼容旧 ms-playwright）
set "SRC1=%LOCALAPPDATA%\ms-playwright-patchright"
set "SRC2=%LOCALAPPDATA%\ms-playwright"
if exist "%SRC1%" (
  robocopy "%SRC1%" dist\browsers /e /nfl /ndl /njh /njs >nul
) else if exist "%SRC2%" (
  robocopy "%SRC2%" dist\browsers /e /nfl /ndl /njh /njs >nul
) else (
  echo [build] 未找到浏览器目录，请先运行: patchright install chromium
  exit /b 1
)

echo [build] 完成。dist\ 即发布包，整个目录拷贝到目标机器即可运行 monitor.exe
endlocal

@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动 CBETS-B2C 控制台，浏览器会在几秒后自动打开...
echo 关闭这个窗口就等于停止服务。
echo.
start "" /min cmd /c "timeout /t 5 >nul & start http://127.0.0.1:8000/console/index.html"
python -m uvicorn main:app --host 127.0.0.1 --port 8000
echo.
echo 服务已停止，按任意键关闭。
pause >nul
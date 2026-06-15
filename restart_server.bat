@echo off
echo ===================================
echo  FIFA 2026 服务器重启脚本
echo ===================================
echo.

echo [1/3] 正在停止旧服务器...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8086 ^| findstr LISTENING') do (
    echo 正在停止进程 PID: %%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [2/3] 正在启动新服务器...
start /B python server.py

timeout /t 3 /nobreak >nul

echo [3/3] 验证服务器状态...
netstat -aon | findstr :8086 | findstr LISTENING >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo ✅ 服务器启动成功！
    echo.
    echo 📊 访问统计页面: http://192.168.0.10:8086/stats
    echo 🏠 主页: http://192.168.0.10:8086
    echo 📱 APK下载: http://192.168.0.10:8086/apk/文件名.apk
    echo.
) else (
    echo.
    echo ❌ 服务器启动失败，请检查错误信息
    echo.
)

pause

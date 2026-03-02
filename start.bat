@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================
::  Fin 量化交易系统 — 启动服务 (Windows)
::  只启动核心服务：Python 后端 + 前端
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%backend"
set "FRONTEND_DIR=%SCRIPT_DIR%frontend"

echo.
echo === Fin 量化交易系统 - 启动服务 ===
echo.

:: ============================================================
:: [1/3] 停止已有服务
:: ============================================================
echo [1/3] 停止已有服务...

taskkill /F /FI "WINDOWTITLE eq fin-backend" >nul 2>&1
taskkill /F /FI "IMAGENAME eq uvicorn.exe" >nul 2>&1
:: 杀掉 node vite 进程（通过窗口标题匹配）
taskkill /F /FI "WINDOWTITLE eq fin-frontend" >nul 2>&1

:: 也通过端口杀
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":5174.*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
)

timeout /t 2 /nobreak >nul
echo   已清理
echo.

:: ============================================================
:: [2/3] 启动后端
:: ============================================================
echo [2/3] 启动后端 (port 8000)...

cd /d "%BACKEND_DIR%"
start "fin-backend" /MIN cmd /c "conda run --no-banner -n quant python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 2>&1 | tee %TEMP%\fin-backend.log"
echo   日志: %TEMP%\fin-backend.log
echo.

:: ============================================================
:: [3/3] 启动前端
:: ============================================================
echo [3/3] 启动前端 (vite dev)...

cd /d "%FRONTEND_DIR%"
start "fin-frontend" /MIN cmd /c "npm run dev 2>&1 | tee %TEMP%\fin-frontend.log"
echo   日志: %TEMP%\fin-frontend.log
echo.

:: 等待启动
timeout /t 3 /nobreak >nul

echo 服务状态:
echo   后端: http://127.0.0.1:8000/docs
echo   前端: http://127.0.0.1:5174
echo.
echo 停止服务: stop.bat
echo.

cd /d "%SCRIPT_DIR%"
endlocal

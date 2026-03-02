@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================
::  Fin 量化交易系统 — 停止服务 (Windows)
:: ============================================================

echo.
echo === Fin 量化交易系统 - 停止服务 ===
echo.

:: 通过窗口标题停止
taskkill /F /FI "WINDOWTITLE eq fin-backend" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq fin-frontend" >nul 2>&1

:: 通过进程名停止
taskkill /F /FI "IMAGENAME eq uvicorn.exe" >nul 2>&1

:: 通过端口停止
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":8000.*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo   后端进程 PID %%p 已停止
)
for /f "tokens=5" %%p in ('netstat -aon 2^>nul ^| findstr ":5174.*LISTENING"') do (
    taskkill /F /PID %%p >nul 2>&1
    echo   前端进程 PID %%p 已停止
)

timeout /t 2 /nobreak >nul

echo.
echo   所有服务已停止
echo.

endlocal

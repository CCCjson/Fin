@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================
::  Fin 量化交易系统 — 一键部署脚本 (Windows)
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%backend"
set "FRONTEND_DIR=%SCRIPT_DIR%frontend"

echo.
echo ========================================
echo   Fin 量化交易系统 - 一键部署 (Windows)
echo ========================================
echo.

:: ============================================================
:: [1/6] 检测系统环境
:: ============================================================
echo [1/6] 检测系统环境...

set "OS_NAME=Windows"
set "ARCH=x86_64"
set "GPU=CPU"

:: 检测 NVIDIA GPU
nvidia-smi --query-gpu=name --format=csv,noheader >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%i in ('nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul') do (
        set "GPU=NVIDIA %%i"
        goto :gpu_done
    )
)
:gpu_done

echo   操作系统: %OS_NAME%
echo   架构:     %ARCH%
echo   GPU:      %GPU%
echo.

:: ============================================================
:: [2/6] 安装 Miniconda
:: ============================================================
echo [2/6] 检查 Miniconda...

where conda >nul 2>&1
if !errorlevel! equ 0 (
    echo   已安装 conda
) else (
    echo   未检测到 conda，开始安装 Miniconda...
    set "INSTALLER=%TEMP%\Miniconda3-latest-Windows-x86_64.exe"
    echo   下载 Miniconda...
    powershell -Command "Invoke-WebRequest -Uri 'https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe' -OutFile '!INSTALLER!'"
    if not exist "!INSTALLER!" (
        echo   [错误] 下载失败
        exit /b 1
    )
    echo   静默安装中（可能需要几分钟）...
    start /wait "" "!INSTALLER!" /S /D=%USERPROFILE%\miniconda3
    del /f "!INSTALLER!" 2>nul

    :: 添加到 PATH
    set "PATH=%USERPROFILE%\miniconda3;%USERPROFILE%\miniconda3\Scripts;%USERPROFILE%\miniconda3\Library\bin;%PATH%"

    where conda >nul 2>&1
    if !errorlevel! neq 0 (
        echo   [错误] conda 安装失败，请手动安装 Miniconda
        exit /b 1
    )
    echo   Miniconda 安装完成
    echo   [注意] 请重新打开命令行窗口使 conda 生效
)
echo.

:: ============================================================
:: [3/6] 创建 conda 环境 quant (Python 3.12)
:: ============================================================
echo [3/6] 配置 conda 环境 quant...

conda env list 2>nul | findstr /B "quant " >nul 2>&1
if !errorlevel! equ 0 (
    echo   环境已存在，跳过创建
) else (
    echo   创建 conda 环境 quant (Python 3.12^)...
    conda create -n quant python=3.12 -y
    if !errorlevel! neq 0 (
        echo   [错误] 环境创建失败
        exit /b 1
    )
    echo   环境创建完成
)
echo.

:: ============================================================
:: [4/6] 安装 Python 依赖
:: ============================================================
echo [4/6] 安装 Python 依赖...

if not exist "%BACKEND_DIR%\requirements.txt" (
    echo   [错误] 找不到 backend\requirements.txt
    exit /b 1
)

:: 创建临时 requirements（排除 torch）
set "TEMP_REQ=%TEMP%\req_no_torch.txt"
findstr /V /B "torch" "%BACKEND_DIR%\requirements.txt" > "%TEMP_REQ%"

echo   安装基础依赖（排除 torch）...
conda run -n quant pip install -r "%TEMP_REQ%" 2>&1 | findstr /V "already satisfied"
del /f "%TEMP_REQ%" 2>nul

:: PyTorch 特殊处理
echo   安装 PyTorch...
echo %GPU% | findstr /I "NVIDIA" >nul 2>&1
if !errorlevel! equ 0 (
    echo   NVIDIA GPU: 安装 CUDA 版 PyTorch
    conda run -n quant pip install torch --index-url https://download.pytorch.org/whl/cu121
) else (
    echo   无 GPU: 安装 CPU 版 PyTorch
    conda run -n quant pip install torch --index-url https://download.pytorch.org/whl/cpu
)

echo   Python 依赖安装完成
echo.

:: ============================================================
:: [5/6] 安装 Node.js 和前端依赖
:: ============================================================
echo [5/6] 检查 Node.js 和前端依赖...

where node >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%v in ('node --version') do echo   Node.js 已安装: %%v
) else (
    echo   未检测到 Node.js，开始安装...
    set "NODE_INSTALLER=%TEMP%\node-setup.msi"
    echo   下载 Node.js 20.x...
    powershell -Command "Invoke-WebRequest -Uri 'https://nodejs.org/dist/v20.11.0/node-v20.11.0-x64.msi' -OutFile '!NODE_INSTALLER!'"
    if not exist "!NODE_INSTALLER!" (
        echo   [错误] Node.js 下载失败
        exit /b 1
    )
    echo   静默安装 Node.js...
    msiexec /i "!NODE_INSTALLER!" /qn /norestart
    del /f "!NODE_INSTALLER!" 2>nul
    :: 刷新 PATH
    set "PATH=%ProgramFiles%\nodejs;%PATH%"
    echo   Node.js 安装完成
)

:: 安装前端依赖
if exist "%FRONTEND_DIR%" (
    echo   安装前端依赖 (npm install^)...
    cd /d "%FRONTEND_DIR%"
    call npm install
    cd /d "%SCRIPT_DIR%"
    echo   前端依赖安装完成
) else (
    echo   [错误] 找不到 frontend\ 目录
    exit /b 1
)
echo.

:: ============================================================
:: [6/6] 初始化配置
:: ============================================================
echo [6/6] 初始化配置...

:: 复制 .env.example → .env
if not exist "%BACKEND_DIR%\.env" (
    if exist "%BACKEND_DIR%\.env.example" (
        copy "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
        echo   已创建 backend\.env（请编辑填入 API key 等配置）
    ) else (
        echo   未找到 .env.example，跳过
    )
) else (
    echo   backend\.env 已存在，跳过
)

:: 创建必要目录
if not exist "%BACKEND_DIR%\data" mkdir "%BACKEND_DIR%\data"
if not exist "%BACKEND_DIR%\logs" mkdir "%BACKEND_DIR%\logs"
if not exist "%BACKEND_DIR%\finetune\adapters" mkdir "%BACKEND_DIR%\finetune\adapters"
if not exist "%BACKEND_DIR%\finetune\fused_model" mkdir "%BACKEND_DIR%\finetune\fused_model"
echo   已确认 data\ logs\ finetune\ 目录存在

echo.
echo ========================================
echo   部署完成！
echo ========================================
echo.
echo   系统:  %OS_NAME% %ARCH%
echo   GPU:   %GPU%
echo.
echo   下一步:
echo   1. 编辑 backend\.env 填入 API key 等配置
echo   2. 运行 start.bat 启动服务
echo.

endlocal

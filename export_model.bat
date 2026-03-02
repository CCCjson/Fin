@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================
::  Fin 量化交易系统 — 导出模型 (Windows)
::  打包 LoRA adapters + 融合模型 + 元信息 → zip
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "FINETUNE_DIR=%SCRIPT_DIR%backend\finetune"
set "ADAPTERS_DIR=%FINETUNE_DIR%\adapters"
set "FUSED_DIR=%FINETUNE_DIR%\fused_model"
set "DATA_DIR=%FINETUNE_DIR%\data\final"

echo.
echo === Fin 量化交易系统 - 导出模型 ===
echo.

:: 检查可导出内容
set "HAS_ADAPTERS=0"
set "HAS_FUSED=0"
set "HAS_DATA=0"

if exist "%ADAPTERS_DIR%\*.safetensors" set "HAS_ADAPTERS=1"
if exist "%ADAPTERS_DIR%\*.json" set "HAS_ADAPTERS=1"
if exist "%ADAPTERS_DIR%\*.yaml" set "HAS_ADAPTERS=1"

:: 检查 fused_model 是否有非 .gitkeep 文件
set "FUSED_COUNT=0"
if exist "%FUSED_DIR%" (
    for %%f in ("%FUSED_DIR%\*") do (
        if /I not "%%~nxf"==".gitkeep" set "HAS_FUSED=1"
    )
)

if exist "%DATA_DIR%\*.jsonl" set "HAS_DATA=1"

if !HAS_ADAPTERS! equ 1 echo   [OK] adapters\
if !HAS_FUSED! equ 1 echo   [OK] fused_model\
if !HAS_DATA! equ 1 echo   [??] data\final\ [可选]

if !HAS_ADAPTERS! equ 0 if !HAS_FUSED! equ 0 (
    echo   [错误] 未找到可导出的模型文件
    echo   请确认以下目录有模型文件:
    echo     %ADAPTERS_DIR%\
    echo     %FUSED_DIR%\
    exit /b 1
)

:: 询问是否包含训练数据
set "INCLUDE_DATA=false"
if !HAS_DATA! equ 1 (
    echo.
    set /p "REPLY=  是否包含训练数据？(y/N) "
    if /I "!REPLY!"=="y" set "INCLUDE_DATA=true"
)

:: 生成时间戳和文件名
for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value 2^>nul') do set "DT=%%a"
set "TIMESTAMP=%DT:~0,8%_%DT:~8,6%"
set "ZIP_NAME=fin_model_export_%TIMESTAMP%.zip"
set "ZIP_PATH=%SCRIPT_DIR%%ZIP_NAME%"
set "TEMP_EXPORT=%TEMP%\fin_model_export"

:: 清理临时目录
if exist "%TEMP_EXPORT%" rmdir /S /Q "%TEMP_EXPORT%"
mkdir "%TEMP_EXPORT%"

echo.
echo 打包中...

:: 复制 adapters
if !HAS_ADAPTERS! equ 1 (
    mkdir "%TEMP_EXPORT%\adapters" 2>nul
    for %%f in ("%ADAPTERS_DIR%\*") do (
        if /I not "%%~nxf"==".gitkeep" copy "%%f" "%TEMP_EXPORT%\adapters\" >nul 2>&1
    )
    echo   + adapters\
)

:: 复制 fused_model
if !HAS_FUSED! equ 1 (
    mkdir "%TEMP_EXPORT%\fused_model" 2>nul
    for %%f in ("%FUSED_DIR%\*") do (
        if /I not "%%~nxf"==".gitkeep" copy "%%f" "%TEMP_EXPORT%\fused_model\" >nul 2>&1
    )
    echo   + fused_model\
)

:: 复制训练数据
if "!INCLUDE_DATA!"=="true" (
    mkdir "%TEMP_EXPORT%\data\final" 2>nul
    copy "%DATA_DIR%\*.jsonl" "%TEMP_EXPORT%\data\final\" >nul 2>&1
    echo   + data\final\
)

:: 检测 GPU
set "GPU_DISPLAY=CPU"
nvidia-smi --query-gpu=name --format=csv,noheader >nul 2>&1
if !errorlevel! equ 0 (
    for /f "delims=" %%g in ('nvidia-smi --query-gpu=name --format=csv,noheader 2^>nul') do (
        set "GPU_DISPLAY=NVIDIA %%g"
        goto :gpu_export_done
    )
)
:gpu_export_done

:: 生成 metadata.json
set "TODAY=%DT:~0,4%-%DT:~4,2%-%DT:~6,2%"
(
echo {
echo   "export_date": "%TODAY%",
echo   "export_timestamp": "%TIMESTAMP%",
echo   "machine": "%COMPUTERNAME%",
echo   "os": "Windows",
echo   "arch": "%PROCESSOR_ARCHITECTURE%",
echo   "gpu": "%GPU_DISPLAY%",
echo   "base_model": "unknown",
echo   "training_iters": "unknown",
echo   "includes_data": %INCLUDE_DATA%,
echo   "includes_adapters": true,
echo   "includes_fused_model": true
echo }
) > "%TEMP_EXPORT%\metadata.json"
echo   + metadata.json

:: 使用 PowerShell 打包 zip
echo.
echo 压缩中...
powershell -Command "Compress-Archive -Path '%TEMP_EXPORT%\*' -DestinationPath '%ZIP_PATH%' -Force"

:: 清理
rmdir /S /Q "%TEMP_EXPORT%" 2>nul

:: 输出结果
for %%f in ("%ZIP_PATH%") do set "ZIP_SIZE=%%~zf"
set /a "ZIP_SIZE_MB=!ZIP_SIZE! / 1048576"

echo.
echo ========================================
echo   导出完成！
echo ========================================
echo.
echo   文件: %ZIP_PATH%
echo   大小: ~!ZIP_SIZE_MB! MB
echo.
echo   在目标机器上导入:
echo     import_model.bat %ZIP_NAME%
echo.

endlocal

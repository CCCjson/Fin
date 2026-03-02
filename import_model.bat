@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: ============================================================
::  Fin 量化交易系统 — 导入模型 (Windows)
::  从 zip 解压 LoRA adapters + 融合模型到正确位置
:: ============================================================

set "SCRIPT_DIR=%~dp0"
set "FINETUNE_DIR=%SCRIPT_DIR%backend\finetune"
set "ADAPTERS_DIR=%FINETUNE_DIR%\adapters"
set "FUSED_DIR=%FINETUNE_DIR%\fused_model"

echo.
echo === Fin 量化交易系统 - 导入模型 ===
echo.

:: 检查参数
if "%~1"=="" (
    echo   用法: import_model.bat ^<zip文件路径^>
    echo.
    echo   示例:
    echo     import_model.bat fin_model_export_20260302_143000.zip
    echo     import_model.bat C:\path\to\model.zip
    exit /b 1
)

set "ZIP_FILE=%~1"

:: 支持相对路径
if not exist "%ZIP_FILE%" (
    set "ZIP_FILE=%SCRIPT_DIR%%~1"
)

if not exist "%ZIP_FILE%" (
    echo   [错误] 文件不存在: %ZIP_FILE%
    exit /b 1
)

echo   导入文件: %~nx1
echo.

:: ============================================================
:: [1/3] 解压 zip
:: ============================================================
echo [1/3] 解压文件...

set "TEMP_EXPORT=%TEMP%\fin_model_import"
if exist "%TEMP_EXPORT%" rmdir /S /Q "%TEMP_EXPORT%"
mkdir "%TEMP_EXPORT%"

powershell -Command "Expand-Archive -Path '%ZIP_FILE%' -DestinationPath '%TEMP_EXPORT%' -Force"

:: 查找解压根目录
set "EXPORT_ROOT=%TEMP_EXPORT%"
if exist "%TEMP_EXPORT%\fin_model_export\metadata.json" (
    set "EXPORT_ROOT=%TEMP_EXPORT%\fin_model_export"
)

echo   解压完成

:: 显示 metadata
if exist "!EXPORT_ROOT!\metadata.json" (
    echo.
    echo   导出信息:
    type "!EXPORT_ROOT!\metadata.json"
    echo.
)
echo.

:: ============================================================
:: [2/3] 复制文件到目标位置
:: ============================================================
echo [2/3] 导入模型文件...

set "IMPORTED=0"

:: 导入 adapters
if exist "!EXPORT_ROOT!\adapters" (
    if not exist "%ADAPTERS_DIR%" mkdir "%ADAPTERS_DIR%"
    :: 备份现有的
    if exist "%ADAPTERS_DIR%\*.safetensors" (
        for /f "tokens=2 delims==" %%a in ('wmic os get localdatetime /value 2^>nul') do set "BDT=%%a"
        set "BACKUP_DIR=%ADAPTERS_DIR%.backup_!BDT:~0,8!_!BDT:~8,6!"
        echo   备份现有 adapters...
        xcopy "%ADAPTERS_DIR%" "!BACKUP_DIR!\" /E /I /Q >nul 2>&1
    )
    xcopy "!EXPORT_ROOT!\adapters\*" "%ADAPTERS_DIR%\" /Y /Q >nul 2>&1
    echo   [OK] adapters\
    set /a "IMPORTED+=1"
)

:: 导入 fused_model
if exist "!EXPORT_ROOT!\fused_model" (
    if not exist "%FUSED_DIR%" mkdir "%FUSED_DIR%"
    xcopy "!EXPORT_ROOT!\fused_model\*" "%FUSED_DIR%\" /Y /Q >nul 2>&1
    echo   [OK] fused_model\
    set /a "IMPORTED+=1"
)

:: 导入训练数据
if exist "!EXPORT_ROOT!\data\final" (
    set "DATA_TARGET=%FINETUNE_DIR%\data\final"
    if not exist "!DATA_TARGET!" mkdir "!DATA_TARGET!"
    xcopy "!EXPORT_ROOT!\data\final\*" "!DATA_TARGET!\" /Y /Q >nul 2>&1
    echo   [OK] data\final\
    set /a "IMPORTED+=1"
)

:: ============================================================
:: [3/3] 清理
:: ============================================================
echo.
echo [3/3] 清理临时文件...
rmdir /S /Q "%TEMP_EXPORT%" 2>nul

echo.
if !IMPORTED! gtr 0 (
    echo ========================================
    echo   导入完成！(共 !IMPORTED! 个模块^)
    echo ========================================
    echo.
    echo   模型位置:
    echo     adapters:    %ADAPTERS_DIR%
    echo     fused_model: %FUSED_DIR%
    echo.
    echo   下一步:
    echo     如需将 adapters 融合到基础模型，运行:
    echo     conda run -n quant python backend\finetune\deploy.py
) else (
    echo   [警告] 未导入任何文件
)
echo.

endlocal

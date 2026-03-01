#!/bin/bash
# ──────────────────────────────────────────────
# QMT Bridge Server 启动脚本
# 通过 CrossOver 的 Wine 运行 bridge_server.py
# ──────────────────────────────────────────────

# CrossOver Wine 可执行文件路径（根据实际安装调整）
WINE="/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/bin/wine"

# Wine Python 路径（miniQMT 自带的 Python，根据实际路径调整）
WINE_PYTHON="C:\\Python38\\python.exe"

# Bridge Server 脚本路径（Wine 中的路径）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BRIDGE_SCRIPT="${SCRIPT_DIR}/bridge_server.py"

echo "========================================"
echo "  QMT Bridge Server 启动器"
echo "========================================"
echo "  Wine: ${WINE}"
echo "  Python: ${WINE_PYTHON}"
echo "  Script: ${BRIDGE_SCRIPT}"
echo "========================================"

# 检查 CrossOver 是否存在
if [ ! -f "${WINE}" ]; then
    echo "[ERROR] CrossOver Wine 未找到: ${WINE}"
    echo "请确认 CrossOver 已安装，或修改脚本中的 WINE 路径"
    exit 1
fi

# 启动 Bridge Server
# 注意: Wine 中的路径需要使用 Windows 格式
# 如果 bridge_server.py 在 Mac 文件系统上，Wine 会自动映射为 Z: 盘
exec "${WINE}" "${WINE_PYTHON}" "Z:${BRIDGE_SCRIPT}"

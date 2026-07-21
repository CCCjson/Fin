#!/bin/bash
# 把当前代码的前端打包安装进 /Applications/Fin.app。
# 本机自用：tauri build → ditto 覆盖安装，不涉及签名/公证/分发。
#
# 这是「构建安装」的单一真源，restart.sh 全量档直接调用本脚本，别在别处再抄一遍。
# 本脚本只负责前端产物，不碰后端进程、不打开 App —— 那两件事由 restart.sh 编排
# （避免 App 启动时自己 spawn 的 start-services.sh 与 restart.sh 抢着起后端）。
#
# 用法: bash desktop/build-app.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
FRONTEND_DIR="$ROOT/frontend"
APP_BUNDLE="$FRONTEND_DIR/src-tauri/target/release/bundle/macos/Fin.app"
INSTALL_PATH="/Applications/Fin.app"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

echo -e "${YELLOW}=== 构建并安装 Fin.app ===${NC}"

# ---- 关掉正在跑的 App 窗口（否则 ditto 覆盖正在运行的 bundle 会出怪事）----
pkill -f "${INSTALL_PATH}/Contents/MacOS/app" 2>/dev/null && echo "  已退出旧 App 窗口" || true

# ---- 构建前端静态产物 ----
# 注：tauri.conf.json 的 beforeBuildCommand 也会跑一次 npm run build，幂等无害；
# 这里显式先跑一次，前端出错能早点看见，不用等 Rust 编译完才报。
echo -e "${GREEN}[1/3] 构建前端 (npm run build)...${NC}"
cd "$FRONTEND_DIR"
npm run build

# ---- 打包 Tauri App（静态产物会被编译进 Rust 二进制，这就是 App 端没有热更新的原因）----
echo -e "${GREEN}[2/3] 打包桌面 App (tauri build)...${NC}"
npm run app:build

if [ ! -d "$APP_BUNDLE" ]; then
    echo -e "${RED}✗ 打包产物不存在: ${APP_BUNDLE}${NC}"
    exit 1
fi

# ---- 覆盖安装到 /Applications ----
echo -e "${GREEN}[3/3] 安装到 ${INSTALL_PATH}...${NC}"
rm -rf "$INSTALL_PATH"
ditto "$APP_BUNDLE" "$INSTALL_PATH"

echo -e "${GREEN}✓ 已安装 ${INSTALL_PATH}${NC}"
echo "  （首次运行如遇 Gatekeeper「无法验证开发者」，右键点「打开」确认一次即可）"
echo "  单独跑本脚本只更新了 App 界面；要连后端一起重启请用: bash restart.sh"

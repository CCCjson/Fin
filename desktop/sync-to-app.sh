#!/bin/bash
# 把 web 端验证过的 master 代码同步（promote）到桌面 App。
# 本地自用打包：tauri build → 覆盖安装到 /Applications/Fin.app，不涉及签名/公证/分发。
# 用法: bash desktop/sync-to-app.sh

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

cd "$ROOT"

echo -e "${YELLOW}=== Promote master -> app ===${NC}"

# ---- 前置检查：必须在 master 且 working tree 干净 ----
# app 分支只由本脚本移动，不接受手动 commit；promote 前必须先在 master 上提交干净。
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "master" ]; then
    echo -e "${RED}✗ 当前在 ${CURRENT_BRANCH} 分支，不是 master，先切回 master 再 promote${NC}"
    exit 1
fi
if [ -n "$(git status --short)" ]; then
    echo -e "${RED}✗ working tree 不干净，先 commit 或 stash：${NC}"
    git status --short
    exit 1
fi

PROMOTE_SHA=$(git rev-parse --short HEAD)

# ---- 构建前端 ----
echo -e "${GREEN}[1/4] 构建前端 (npm run build)...${NC}"
cd "$FRONTEND_DIR"
npm run build

# ---- 打包 Tauri App（tauri.conf.json 的 beforeBuildCommand 会再跑一次 npm run build，幂等无害）----
echo -e "${GREEN}[2/4] 打包桌面 App (tauri build)...${NC}"
npm run app:build

if [ ! -d "$APP_BUNDLE" ]; then
    echo -e "${RED}✗ 打包产物不存在: ${APP_BUNDLE}${NC}"
    exit 1
fi

# ---- 停掉旧的 App 后端 + 旧的 App 窗口 ----
# 精确匹配端口 8000 / Fin.app 内二进制路径，不会误杀 web 端跑在 8010 的开发后端。
echo -e "${GREEN}[3/4] 停掉旧实例...${NC}"
pkill -f "uvicorn api.main:app.*--port 8000" 2>/dev/null && echo "  旧 app 后端已停止" || echo "  旧 app 后端未在运行"
pkill -f "${INSTALL_PATH}/Contents/MacOS/app" 2>/dev/null && echo "  旧 App 窗口已退出" || echo "  旧 App 未在运行"
sleep 1

# ---- 覆盖安装到 /Applications ----
echo -e "${GREEN}[4/4] 安装到 ${INSTALL_PATH}...${NC}"
rm -rf "$INSTALL_PATH"
ditto "$APP_BUNDLE" "$INSTALL_PATH"

# ---- 构建 + 安装都成功后，才把 app 分支指针快进到这次 promote 的 commit ----
cd "$ROOT"
git branch -f app HEAD

open "$INSTALL_PATH"

echo ""
echo -e "${GREEN}✓ Promote 完成：app 分支 = master @ ${PROMOTE_SHA}${NC}"
echo -e "${YELLOW}还没推到 app 的改动（此刻应为空）:${NC}"
git log app..master --oneline
echo ""
echo "已重新打开 ${INSTALL_PATH}（首次运行如遇 Gatekeeper「无法验证开发者」提示，右键点「打开」确认一次即可，不影响日常使用）"
echo ""
echo -e "${YELLOW}可选：想把 app 分支也备份到远程，自己再跑一次：${NC} git push origin app"

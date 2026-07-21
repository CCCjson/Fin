#!/bin/bash
# ============================================================
#  Fin —— 一键更新并重启（唯一入口）
#
#  2026-07-21 起项目只服务 Mac 桌面 App，web 端（:8010 开发后端 + vite :5174）已整体退役。
#  改完代码跑这个脚本，App 里就是最新的代码 —— 前端没有热更新（静态产物编译进 App 二进制），
#  后端也不带 --reload，全靠这里重启。
#
#  用法:
#    bash restart.sh              全量：重建前端 → 重装 /Applications/Fin.app → 重启后端 → 打开 App
#    bash restart.sh --backend    快档：只重启后端 + C++ 服务（改 Python 时用，约 40s）
#
#  服务分工（本脚本只做编排，具体活分别落在两个脚本里，别在这里重复实现）:
#    desktop/build-app.sh      构建 + 安装 App（npm run build → tauri build → ditto）
#    desktop/start-services.sh 拉起后端(:8000) + C++ 订单簿(:8001) + C++ 回测(:8002) + SSH 隧道(:11434)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_PATH="/Applications/Fin.app"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

# ---- 参数 ----
FULL=true
case "$1" in
    --backend|-b) FULL=false ;;
    "") ;;
    *) echo "未知参数: $1（可用: --backend）"; exit 1 ;;
esac

if [ "$FULL" = true ]; then
    echo -e "${YELLOW}=== Fin 全量更新重启（前端重建 + App 重装 + 后端重启）===${NC}"
else
    echo -e "${YELLOW}=== Fin 后端重启（跳过前端构建，App 界面保持不变）===${NC}"
fi

# ---- 前置检查：T9 必须挂载 ----
# 后端连的是生产库（backend/data 软链到 T9 上的 market.db），datasets / huggingface 缓存同理。
# 没插硬盘就启动，SQLAlchemy 会在空目录里建一个空库，看起来能跑但数据全没了 —— 直接拦住。
if [ ! -d /Volumes/T9 ]; then
    echo -e "${RED}✗ 未检测到三星 T9 移动硬盘挂载在 /Volumes/T9${NC}"
    echo -e "${RED}  backend/data(market.db)、datasets、code_dataset 都软链在 T9 上，${NC}"
    echo -e "${RED}  没插硬盘启动会读不到生产数据。请先插上 T9 再重试。${NC}"
    exit 1
fi

# ---- [1] 停掉所有旧进程 ----
echo -e "${RED}[1/4] 停止旧进程...${NC}"
pkill -f "${INSTALL_PATH}/Contents/MacOS/app" 2>/dev/null && echo "  App 窗口已退出"      || echo "  App 未在运行"
# 不按端口过滤：单实例架构下任何 uvicorn api.main:app 都该被这次重启接管，
# 顺带收掉历史遗留（如 web 时代跑在 :8010 的开发后端）与临时调试实例。
pkill -f "uvicorn api.main:app"                2>/dev/null && echo "  后端已停止"          || echo "  后端未在运行"
pkill -f "orderbook_server"                   2>/dev/null && echo "  订单簿服务已停止"    || echo "  订单簿服务未在运行"
pkill -f "backtest_server"                    2>/dev/null && echo "  回测服务已停止"      || echo "  回测服务未在运行"
pkill -f "ssh -N.*11434"                      2>/dev/null && echo "  SSH隧道已停止"       || echo "  SSH隧道未在运行"

# 等端口释放（uvicorn 占的 8000 释放较慢；没释放干净的话下一步的幂等启动会误判「已在跑」而跳过）
echo "  等待端口释放..."
for i in $(seq 1 15); do
    if ! lsof -i :8000 -t >/dev/null 2>&1 \
        && ! lsof -i :8001 -t >/dev/null 2>&1 \
        && ! lsof -i :8002 -t >/dev/null 2>&1 \
        && ! lsof -i :11434 -t >/dev/null 2>&1; then
        echo "  端口已释放"
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo -e "${RED}  警告: 端口释放超时，强制杀掉占用进程...${NC}"
        for p in 8000 8001 8002 11434; do
            lsof -i :$p -t 2>/dev/null | xargs kill -9 2>/dev/null
        done
        sleep 1
    fi
    sleep 1
done

# ---- [2] 构建并安装 App（--backend 时跳过）----
if [ "$FULL" = true ]; then
    echo -e "${GREEN}[2/4] 重建前端并重装 App（tauri build，约 1-2 分钟）...${NC}"
    if ! bash "$SCRIPT_DIR/desktop/build-app.sh"; then
        echo -e "${RED}✗ 构建失败，已停在这一步；服务未启动。修好后重跑 bash restart.sh${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}[2/4] 跳过前端构建（--backend）${NC}"
fi

# ---- [3] 拉起后端 + C++ 服务 + SSH 隧道 ----
# 上一步已经把这些进程全杀干净，start-services.sh 的「已在跑就跳过」此刻等价于全量启动。
echo -e "${GREEN}[3/4] 启动服务...${NC}"
bash "$SCRIPT_DIR/desktop/start-services.sh"

# ---- [4] 打开 App ----
# App 自己也会 spawn 一次 start-services.sh（见 src-tauri/src/lib.rs），
# 此时服务都在跑，它会逐个跳过 —— 所以必须放在服务起来之后开，否则两边抢着起后端。
echo -e "${GREEN}[4/4] 打开 App...${NC}"
open "$INSTALL_PATH"

echo ""
echo -e "${YELLOW}查看日志:${NC}"
echo "  tail -f /tmp/fin-backend.log        后端"
echo "  tail -f /tmp/fin-orderbook.log      C++ 订单簿"
echo "  tail -f /tmp/fin-backtest.log       C++ 回测"
echo ""
echo -e "${YELLOW}停止全部服务:${NC}  bash desktop/stop-services.sh"

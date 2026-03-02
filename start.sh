#!/bin/bash
# ============================================================
#  Fin 量化交易系统 — 启动服务 (macOS / Linux)
#  只启动核心服务：Python 后端 + 前端
#  C++ 服务 / MLX 模型是 Mac 专属可选服务，用 restart.sh 启动
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}=== Fin 量化交易系统 — 启动服务 ===${NC}"
echo ""

# ============================================================
# [1/3] 停止已有服务
# ============================================================
echo -e "${YELLOW}[1/3] 停止已有服务...${NC}"

pkill -f "uvicorn api.main:app" 2>/dev/null && echo "  后端已停止" || echo "  后端未在运行"
pkill -f "node.*vite" 2>/dev/null && echo "  前端已停止" || echo "  前端未在运行"

# 等待端口释放
for i in $(seq 1 10); do
    if ! lsof -i :8000 -t >/dev/null 2>&1 && ! lsof -i :5174 -t >/dev/null 2>&1; then
        break
    fi
    if [ "$i" -eq 10 ]; then
        echo -e "${RED}  端口释放超时，强制清理...${NC}"
        lsof -i :8000 -t 2>/dev/null | xargs kill -9 2>/dev/null
        lsof -i :5174 -t 2>/dev/null | xargs kill -9 2>/dev/null
        sleep 1
    fi
    sleep 1
done
echo ""

# ============================================================
# [2/3] 启动后端
# ============================================================
echo -e "${YELLOW}[2/3] 启动后端 (port 8000)...${NC}"
cd "$BACKEND_DIR"
nohup conda run --no-banner -n quant python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 > /tmp/fin-backend.log 2>&1 &
BACKEND_PID=$!
echo "  PID: $BACKEND_PID  日志: /tmp/fin-backend.log"
echo ""

# ============================================================
# [3/3] 启动前端
# ============================================================
echo -e "${YELLOW}[3/3] 启动前端 (vite dev)...${NC}"
cd "$FRONTEND_DIR"
nohup npm run dev > /tmp/fin-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "  PID: $FRONTEND_PID  日志: /tmp/fin-frontend.log"
echo ""

# 等待启动
sleep 3

# 检查状态
echo -e "${BOLD}服务状态:${NC}"
if kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "  ${GREEN}✓ 后端运行中${NC}  http://127.0.0.1:8000/docs"
else
    echo -e "  ${RED}✗ 后端启动失败${NC}  查看日志: tail -f /tmp/fin-backend.log"
fi

if kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "  ${GREEN}✓ 前端运行中${NC}  http://127.0.0.1:5174"
else
    echo -e "  ${RED}✗ 前端启动失败${NC}  查看日志: tail -f /tmp/fin-frontend.log"
fi

echo ""
echo -e "${YELLOW}查看日志:${NC}"
echo "  tail -f /tmp/fin-backend.log"
echo "  tail -f /tmp/fin-frontend.log"
echo ""
echo -e "${YELLOW}停止服务:${NC}  bash stop.sh"
echo ""

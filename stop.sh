#!/bin/bash
# ============================================================
#  Fin 量化交易系统 — 停止服务 (macOS / Linux)
# ============================================================

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}=== Fin 量化交易系统 — 停止服务 ===${NC}"
echo ""

STOPPED=0

# 后端
if pkill -f "uvicorn api.main:app" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} 后端已停止"
    STOPPED=$((STOPPED + 1))
else
    echo "  后端未在运行"
fi

# 前端
if pkill -f "node.*vite" 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} 前端已停止"
    STOPPED=$((STOPPED + 1))
else
    echo "  前端未在运行"
fi

# 等端口释放
if [ $STOPPED -gt 0 ]; then
    echo ""
    echo "  等待端口释放..."
    for i in $(seq 1 10); do
        if ! lsof -i :8000 -t >/dev/null 2>&1 && ! lsof -i :5174 -t >/dev/null 2>&1; then
            echo -e "  ${GREEN}端口已释放${NC}"
            break
        fi
        if [ "$i" -eq 10 ]; then
            echo -e "  ${YELLOW}超时，强制清理端口...${NC}"
            lsof -i :8000 -t 2>/dev/null | xargs kill -9 2>/dev/null
            lsof -i :5174 -t 2>/dev/null | xargs kill -9 2>/dev/null
        fi
        sleep 1
    done
fi

echo ""
echo -e "  ${GREEN}所有服务已停止${NC}"
echo ""

#!/bin/bash
# Fin 桌面应用 —— 手动停止所有后端服务
# 注意：Tauri 关窗【不会】调用本脚本（服务设计为常驻）。
# 仅当你想彻底停掉全部服务时手动运行。

GREEN='\033[0;32m'
NC='\033[0m'

echo "停止 Fin 所有服务..."
pkill -f "uvicorn api.main:app"  2>/dev/null && echo "  后端已停止"       || echo "  后端未在运行"
pkill -f "node.*vite"            2>/dev/null && echo "  前端已停止"       || echo "  前端未在运行"
pkill -f "orderbook_server"      2>/dev/null && echo "  订单簿服务已停止"  || echo "  订单簿服务未在运行"
pkill -f "backtest_server"       2>/dev/null && echo "  回测服务已停止"    || echo "  回测服务未在运行"
pkill -f "ssh -N.*11434"         2>/dev/null && echo "  SSH隧道已停止"     || echo "  SSH隧道未在运行"

echo -e "${GREEN}完成。${NC}"

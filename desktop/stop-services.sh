#!/bin/bash
# Fin 桌面应用 —— 手动停止所有后端服务
# 注意：Tauri 关窗【不会】调用本脚本（服务设计为常驻）。
# 仅当你想彻底停掉全部服务时手动运行。

GREEN='\033[0;32m'
NC='\033[0m'

echo "停止 Fin App 服务（不影响 web 端 restart.sh 起的 8010 开发实例）..."
# 精确匹配 --port 8000，避免误杀 web 端跑在 8010 的开发后端
pkill -f "uvicorn api.main:app.*--port 8000" 2>/dev/null && echo "  后端已停止"       || echo "  后端未在运行"
pkill -f "orderbook_server"      2>/dev/null && echo "  订单簿服务已停止"  || echo "  订单簿服务未在运行"
pkill -f "backtest_server"       2>/dev/null && echo "  回测服务已停止"    || echo "  回测服务未在运行"
pkill -f "ssh -N.*11434"         2>/dev/null && echo "  SSH隧道已停止"     || echo "  SSH隧道未在运行"

echo -e "${GREEN}完成。${NC}"

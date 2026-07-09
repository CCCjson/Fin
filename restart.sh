#!/bin/bash
# 重启前后端服务（quant conda 环境）

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
ORDERBOOK_DIR="$SCRIPT_DIR/orderbook_simulator"
BACKTEST_DIR="$SCRIPT_DIR/backtest_cpp"
PYTHON="/opt/homebrew/Caskroom/miniconda/base/envs/quant/bin/python"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

# 等待端口进入 LISTEN 状态（$1=端口 $2=最大等待秒数，默认30）
wait_port() {
    local port=$1 max=${2:-30} i
    for ((i=1; i<=max; i++)); do
        lsof -i :"$port" -sTCP:LISTEN -t >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

# 等待 HTTP 返回 200（$1=url $2=最大等待秒数，默认60）
wait_http() {
    local url=$1 max=${2:-60} i code
    for ((i=1; i<=max; i++)); do
        code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
        [ "$code" = "200" ] && return 0
        sleep 1
    done
    return 1
}

echo -e "${YELLOW}=== 量化交易系统 重启脚本（web 开发实例：8010 端口 + 本地测试库，不碰 T9 生产数据）===${NC}"

mkdir -p "$BACKEND_DIR/data_dev"

# 杀掉旧进程
echo -e "${RED}[1/6] 停止旧进程...${NC}"
# 后端: 精确匹配 --port 8010，避免误杀 app 端跑在 8000 的稳定后端
pkill -f "uvicorn api.main:app.*--port 8010" 2>/dev/null && echo "  后端已停止" || echo "  后端未在运行"
# 前端: vite (直接匹配 node.*vite，不用 --port 因为端口写在 config 里)
pkill -f "node.*vite" 2>/dev/null && echo "  前端已停止" || echo "  前端未在运行"
# C++ 订单簿服务
pkill -f "orderbook_server" 2>/dev/null && echo "  订单簿服务已停止" || echo "  订单簿服务未在运行"
# C++ 回测服务
pkill -f "backtest_server" 2>/dev/null && echo "  回测服务已停止" || echo "  回测服务未在运行"
# SSH 隧道（远程 GPU 模型服务）
pkill -f "ssh -N.*11434" 2>/dev/null && echo "  SSH隧道已停止" || echo "  SSH隧道未在运行"
# 等端口释放（特别是 uvicorn 占用的 8010 端口可能释放较慢）
echo "  等待端口释放..."
for i in $(seq 1 15); do
    if ! lsof -i :8010 -t >/dev/null 2>&1 && ! lsof -i :5174 -t >/dev/null 2>&1 && ! lsof -i :11434 -t >/dev/null 2>&1; then
        echo "  端口已释放"
        break
    fi
    if [ $i -eq 15 ]; then
        echo -e "${RED}  警告: 端口释放超时，尝试强制杀掉占用进程...${NC}"
        lsof -i :8010 -t 2>/dev/null | xargs kill -9 2>/dev/null
        lsof -i :5174 -t 2>/dev/null | xargs kill -9 2>/dev/null
        lsof -i :11434 -t 2>/dev/null | xargs kill -9 2>/dev/null
        sleep 1
    fi
    sleep 1
done

# 建立 SSH 隧道连接远程 GPU（自动探测：公司内网 → 家里穿透）
echo -e "${GREEN}[2/6] 建立 SSH 隧道到远程 GPU (port 11434)...${NC}"
SSH_TUNNEL_OK=false
# 先试公司内网
if nc -z -w 3 192.168.3.10 22 2>/dev/null; then
    echo "  检测到公司内网"
    ssh -N -f -L 11434:localhost:11434 deepoptica@192.168.3.10 2>/dev/null && SSH_TUNNEL_OK=true
fi
# 不通则试家里穿透
if [ "$SSH_TUNNEL_OK" = false ]; then
    echo "  公司内网不可达，尝试内网穿透..."
    ssh -N -f -L 11434:localhost:11434 -p 2222 deepoptica@120.27.226.222 2>/dev/null && SSH_TUNNEL_OK=true
fi
if [ "$SSH_TUNNEL_OK" = true ]; then
    echo "  SSH 隧道已建立 → 远程 RTX 5070 (qwen3:14b)"
else
    echo -e "${RED}  SSH 隧道建立失败，请检查远程机器是否在线${NC}"
fi

# 编译 & 启动 C++ 订单簿服务
echo -e "${GREEN}[3/6] 启动 C++ 订单簿服务 (port 8001)...${NC}"
if [ -d "$ORDERBOOK_DIR" ]; then
    mkdir -p "$ORDERBOOK_DIR/build"
    cd "$ORDERBOOK_DIR/build"
    # 仅在没有 Makefile 或 CMakeLists.txt 有更新时重新 cmake
    if [ ! -f Makefile ] || [ "$ORDERBOOK_DIR/CMakeLists.txt" -nt Makefile ]; then
        echo "  运行 cmake 配置..."
        cmake .. -DCMAKE_BUILD_TYPE=Release > /tmp/fin-orderbook-cmake.log 2>&1
    fi
    echo "  编译中..."
    make -j$(sysctl -n hw.ncpu) > /tmp/fin-orderbook-build.log 2>&1
    if [ $? -eq 0 ] && [ -f "$ORDERBOOK_DIR/build/orderbook_server" ]; then
        nohup "$ORDERBOOK_DIR/build/orderbook_server" 8001 > /tmp/fin-orderbook.log 2>&1 &
        ORDERBOOK_PID=$!
        echo "  PID: $ORDERBOOK_PID  日志: /tmp/fin-orderbook.log"
    else
        echo -e "${RED}  编译失败，查看日志: cat /tmp/fin-orderbook-build.log${NC}"
        ORDERBOOK_PID=""
    fi
else
    echo -e "${RED}  orderbook_simulator 目录不存在，跳过${NC}"
    ORDERBOOK_PID=""
fi

# 编译 & 启动 C++ 回测服务
echo -e "${GREEN}[4/6] 启动 C++ 回测服务 (port 8002)...${NC}"
if [ -d "$BACKTEST_DIR" ]; then
    mkdir -p "$BACKTEST_DIR/build"
    cd "$BACKTEST_DIR/build"
    if [ ! -f Makefile ] || [ "$BACKTEST_DIR/CMakeLists.txt" -nt Makefile ]; then
        echo "  运行 cmake 配置..."
        cmake .. -DCMAKE_BUILD_TYPE=Release > /tmp/fin-backtest-cmake.log 2>&1
    fi
    echo "  编译中..."
    make -j$(sysctl -n hw.ncpu) > /tmp/fin-backtest-build.log 2>&1
    if [ $? -eq 0 ] && [ -f "$BACKTEST_DIR/build/backtest_server" ]; then
        nohup "$BACKTEST_DIR/build/backtest_server" 8002 > /tmp/fin-backtest.log 2>&1 &
        BACKTEST_PID=$!
        echo "  PID: $BACKTEST_PID  日志: /tmp/fin-backtest.log"
    else
        echo -e "${RED}  编译失败，查看日志: cat /tmp/fin-backtest-build.log${NC}"
        BACKTEST_PID=""
    fi
else
    echo -e "${RED}  backtest_cpp 目录不存在，跳过${NC}"
    BACKTEST_PID=""
fi

# 启动后端（web 开发实例：8010 端口、保留 --reload、关闭定时任务、指向本地测试库）
echo -e "${GREEN}[5/6] 启动后端 (port 8010，开发实例)...${NC}"
cd "$BACKEND_DIR"
nohup env \
    FIN_DISABLE_SCHEDULERS=true \
    DATABASE_URL="sqlite:///$BACKEND_DIR/data_dev/market_dev.db" \
    KNOWLEDGE_DB_PATH="$BACKEND_DIR/data_dev/knowledge_dev.db" \
    "$PYTHON" -m uvicorn api.main:app --host 0.0.0.0 --port 8010 --reload > /tmp/fin-backend-dev.log 2>&1 &
BACKEND_PID=$!
echo "  PID: $BACKEND_PID  日志: /tmp/fin-backend-dev.log"

# 启动前端
echo -e "${GREEN}[6/6] 启动前端 (vite dev)...${NC}"
cd "$FRONTEND_DIR"
nohup npm run dev > /tmp/fin-frontend.log 2>&1 &
FRONTEND_PID=$!
echo "  PID: $FRONTEND_PID  日志: /tmp/fin-frontend.log"

# 等待各服务真正就绪（轮询端口/HTTP，而非只看进程是否存活）
echo ""
echo -e "${YELLOW}等待服务就绪（后端需加载模型/预热，约 20-40s）...${NC}"

# SSH 隧道：绑定即可用
if lsof -i :11434 -t >/dev/null 2>&1; then
    echo -e "${GREEN}✓ SSH隧道运行中（远程GPU）${NC}  http://127.0.0.1:11434"
else
    echo -e "${RED}✗ SSH隧道未建立，Alpha Lab 将无法使用本地模型${NC}"
fi

# C++ 服务：进程存活 + 端口监听才算就绪
check_cpp() {  # $1=名称 $2=PID $3=端口 $4=日志 $5=url
    local name=$1 pid=$2 port=$3 log=$4 url=$5
    [ -z "$pid" ] && return
    if ! kill -0 "$pid" 2>/dev/null; then
        echo -e "${RED}✗ ${name}进程已退出，查看日志: tail -f ${log}${NC}"
    elif wait_port "$port" 15; then
        echo -e "${GREEN}✓ ${name}运行中${NC}  ${url}"
    else
        echo -e "${RED}✗ ${name}端口 ${port} 未监听，查看日志: tail -f ${log}${NC}"
    fi
}
check_cpp "订单簿服务" "$ORDERBOOK_PID" 8001 /tmp/fin-orderbook.log http://127.0.0.1:8001
check_cpp "回测服务"   "$BACKTEST_PID"  8002 /tmp/fin-backtest.log  http://127.0.0.1:8002

# 后端：轮询 /docs 返回 200 才算真正就绪
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo -e "${RED}✗ 后端进程已退出，查看日志: tail -f /tmp/fin-backend-dev.log${NC}"
elif wait_http http://127.0.0.1:8010/docs 90; then
    echo -e "${GREEN}✓ 后端运行中${NC}  http://127.0.0.1:8010/docs"
else
    echo -e "${RED}✗ 后端 90s 内未就绪，查看日志: tail -f /tmp/fin-backend-dev.log${NC}"
fi

# 前端：轮询 vite 端口 5174
if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo -e "${RED}✗ 前端进程已退出，查看日志: tail -f /tmp/fin-frontend.log${NC}"
elif wait_port 5174 30; then
    echo -e "${GREEN}✓ 前端运行中${NC}  http://127.0.0.1:5174"
else
    echo -e "${RED}✗ 前端端口 5174 未监听，查看日志: tail -f /tmp/fin-frontend.log${NC}"
fi

echo ""
echo -e "${YELLOW}查看日志:${NC}"
echo "  SSH隧道: lsof -i :11434"
echo "  tail -f /tmp/fin-orderbook.log"
echo "  tail -f /tmp/fin-backtest.log"
echo "  tail -f /tmp/fin-backend-dev.log"
echo "  tail -f /tmp/fin-frontend.log"
echo ""
echo -e "${YELLOW}停止服务:${NC}"
echo "  pkill -f 'ssh -N.*11434'; pkill -f 'uvicorn api.main:app.*--port 8010'; pkill -f 'node.*vite'; pkill -f 'orderbook_server'; pkill -f 'backtest_server'"

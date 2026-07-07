#!/bin/bash
# Fin 桌面应用 —— 幂等启动脚本
# 从 restart.sh 派生：每个服务先探端口，已在跑就跳过（不杀不重启）。
# 供 Tauri 外壳在 app 启动时调用，也可手动运行。
# 关窗不会调用本脚本；彻底停服务请用 desktop/stop-services.sh。

# 从 Finder 双击的 .app 启动时 PATH 很精简，这里兜底补全，
# 保证能找到 npm / cmake / node / lsof / ssh 等（brew 与系统路径）。
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
ORDERBOOK_DIR="$ROOT/orderbook_simulator"
BACKTEST_DIR="$ROOT/backtest_cpp"

# Python 解释器（可用环境变量 FIN_PYTHON 覆盖）
PYTHON="${FIN_PYTHON:-/opt/homebrew/Caskroom/miniconda/base/envs/quant/bin/python}"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

# 端口是否已在 LISTEN
is_listening() { lsof -i :"$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

# 等待端口进入 LISTEN（$1=端口 $2=最大秒数，默认30）
wait_port() {
    local port=$1 max=${2:-30} i
    for ((i=1; i<=max; i++)); do
        is_listening "$port" && return 0
        sleep 1
    done
    return 1
}

# 等待 HTTP 返回 200（$1=url $2=最大秒数，默认90）
wait_http() {
    local url=$1 max=${2:-90} i code
    for ((i=1; i<=max; i++)); do
        code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
        [ "$code" = "200" ] && return 0
        sleep 1
    done
    return 1
}

echo -e "${YELLOW}=== Fin 服务启动（幂等）===${NC}"

# ---- SSH 隧道到远程 GPU（port 11434，best-effort，完全后台化）----
# 关键：整段放进后台子 shell 并加 ConnectTimeout，绝不阻塞核心服务启动
# （不在公司/家里网络时，ssh 连不可达主机会卡很久）。Alpha Lab 默认走 Claude API，
# 隧道只影响本地模型这一可选功能。
echo -e "${GREEN}[1/5] SSH 隧道 (port 11434, 后台尝试)...${NC}"
if is_listening 11434; then
    echo "  已在运行，跳过"
else
    (
        SSH_OPTS="-o ConnectTimeout=4 -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
        if nc -z -w 3 192.168.3.10 22 2>/dev/null; then
            ssh $SSH_OPTS -N -f -L 11434:localhost:11434 deepoptica@192.168.3.10 2>/dev/null \
                || ssh $SSH_OPTS -N -f -L 11434:localhost:11434 -p 2222 deepoptica@120.27.226.222 2>/dev/null
        else
            ssh $SSH_OPTS -N -f -L 11434:localhost:11434 -p 2222 deepoptica@120.27.226.222 2>/dev/null
        fi
    ) >/dev/null 2>&1 &
    echo "  已在后台尝试建立隧道（不阻塞，最多几秒）"
fi

# ---- C++ 服务通用启动函数 ----
# $1=名称 $2=目录 $3=端口 $4=二进制名 $5=日志 $6=可选启动参数
start_cpp() {
    local name=$1 dir=$2 port=$3 bin=$4 log=$5 arg=$6
    if is_listening "$port"; then
        echo "  ${name} 已在运行 (:$port)，跳过"
        return
    fi
    if [ ! -d "$dir" ]; then
        echo -e "${RED}  ${dir} 目录不存在，跳过 ${name}${NC}"
        return
    fi
    mkdir -p "$dir/build"
    cd "$dir/build" || return
    if [ ! -f Makefile ] || [ "$dir/CMakeLists.txt" -nt Makefile ]; then
        echo "  ${name}: cmake 配置..."
        cmake .. -DCMAKE_BUILD_TYPE=Release > "/tmp/fin-${bin}-cmake.log" 2>&1
    fi
    echo "  ${name}: 编译..."
    make -j"$(sysctl -n hw.ncpu)" > "/tmp/fin-${bin}-build.log" 2>&1
    if [ $? -eq 0 ] && [ -f "$dir/build/$bin" ]; then
        nohup "$dir/build/$bin" $arg > "$log" 2>&1 &
        echo "  ${name} 已启动 PID $!  日志 $log"
    else
        echo -e "${RED}  ${name} 编译失败，看 /tmp/fin-${bin}-build.log${NC}"
    fi
}

echo -e "${GREEN}[2/5] C++ 订单簿服务 (port 8001)...${NC}"
start_cpp "订单簿服务" "$ORDERBOOK_DIR" 8001 orderbook_server /tmp/fin-orderbook.log 8001

echo -e "${GREEN}[3/5] C++ 回测服务 (port 8002)...${NC}"
start_cpp "回测服务" "$BACKTEST_DIR" 8002 backtest_server /tmp/fin-backtest.log 8002

# ---- 后端 uvicorn（--reload 保留，热重载）----
echo -e "${GREEN}[4/5] 后端 (port 8000)...${NC}"
if is_listening 8000; then
    echo "  已在运行，跳过"
else
    cd "$BACKEND_DIR" || exit 1
    nohup "$PYTHON" -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload > /tmp/fin-backend.log 2>&1 &
    echo "  已启动 PID $!  日志 /tmp/fin-backend.log"
fi

# ---- 前端 vite dev（HMR 来源，保留）----
echo -e "${GREEN}[5/5] 前端 vite dev (port 5174)...${NC}"
if is_listening 5174; then
    echo "  已在运行，跳过"
else
    cd "$FRONTEND_DIR" || exit 1
    nohup npm run dev > /tmp/fin-frontend.log 2>&1 &
    echo "  已启动 PID $!  日志 /tmp/fin-frontend.log"
fi

# ---- 就绪汇总（供手动运行时看；Tauri 侧另有 /docs 轮询）----
echo ""
echo -e "${YELLOW}等待服务就绪...${NC}"
wait_http http://127.0.0.1:8000/docs 90 \
    && echo -e "${GREEN}✓ 后端就绪${NC}  http://127.0.0.1:8000/docs" \
    || echo -e "${RED}✗ 后端 90s 内未就绪，看 /tmp/fin-backend.log${NC}"
wait_port 5174 30 \
    && echo -e "${GREEN}✓ 前端就绪${NC}  http://127.0.0.1:5174" \
    || echo -e "${RED}✗ 前端未就绪，看 /tmp/fin-frontend.log${NC}"
for pc in "订单簿:8001" "回测:8002"; do
    n=${pc%:*}; p=${pc#*:}
    is_listening "$p" && echo -e "${GREEN}✓ ${n}服务就绪${NC} (:$p)" || echo -e "${YELLOW}✗ ${n}服务未监听 (:$p)${NC}"
done

echo ""
echo -e "${GREEN}启动流程完成。${NC}"

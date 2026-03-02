#!/bin/bash
# ============================================================
#  Fin 量化交易系统 — 一键部署脚本 (macOS / Linux)
# ============================================================
set -e

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

print_banner() {
    echo ""
    echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}${BOLD}║     Fin 量化交易系统 — 一键部署          ║${NC}"
    echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${NC}"
    echo ""
}

print_banner

# ============================================================
# [1/6] 检测系统环境
# ============================================================
echo -e "${YELLOW}[1/6] 检测系统环境...${NC}"

OS="$(uname -s)"
ARCH="$(uname -m)"
GPU="CPU"

case "$OS" in
    Darwin)
        OS_NAME="macOS"
        # 检测 Apple Silicon
        if [[ "$ARCH" == "arm64" ]]; then
            CPU_BRAND=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Unknown")
            if echo "$CPU_BRAND" | grep -q "Apple"; then
                GPU="Apple Silicon (MPS)"
            fi
        fi
        ;;
    Linux)
        OS_NAME="Linux"
        # 检测 NVIDIA GPU
        if command -v nvidia-smi &>/dev/null; then
            GPU_INFO=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
            if [ -n "$GPU_INFO" ]; then
                GPU="NVIDIA $GPU_INFO"
            fi
        fi
        ;;
    *)
        echo -e "${RED}不支持的操作系统: $OS${NC}"
        echo "Windows 用户请运行 deploy.bat"
        exit 1
        ;;
esac

echo -e "  操作系统: ${GREEN}$OS_NAME${NC}"
echo -e "  架构:     ${GREEN}$ARCH${NC}"
echo -e "  GPU:      ${GREEN}$GPU${NC}"
echo ""

# ============================================================
# [2/6] 安装 Miniconda
# ============================================================
echo -e "${YELLOW}[2/6] 检查 Miniconda...${NC}"

if command -v conda &>/dev/null; then
    CONDA_PATH=$(which conda)
    echo -e "  ${GREEN}已安装${NC}: $CONDA_PATH"
else
    echo "  未检测到 conda，开始安装 Miniconda..."

    case "${OS_NAME}_${ARCH}" in
        macOS_arm64)  INSTALLER_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-arm64.sh" ;;
        macOS_x86_64) INSTALLER_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-MacOSX-x86_64.sh" ;;
        Linux_x86_64) INSTALLER_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh" ;;
        Linux_aarch64|Linux_arm64) INSTALLER_URL="https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-aarch64.sh" ;;
        *)
            echo -e "${RED}  不支持的平台: ${OS_NAME}_${ARCH}${NC}"
            exit 1
            ;;
    esac

    INSTALLER="/tmp/miniconda_installer.sh"
    echo "  下载: $INSTALLER_URL"
    curl -fsSL "$INSTALLER_URL" -o "$INSTALLER"
    bash "$INSTALLER" -b -p "$HOME/miniconda3"
    rm -f "$INSTALLER"

    # 初始化 conda
    eval "$("$HOME/miniconda3/bin/conda" shell.bash hook)"
    "$HOME/miniconda3/bin/conda" init bash zsh 2>/dev/null || true

    echo -e "  ${GREEN}Miniconda 安装完成${NC}"
    echo -e "  ${YELLOW}注意: 请重新打开终端或运行 'source ~/.bashrc' 使 conda 生效${NC}"

    # 确保当前会话可以用
    export PATH="$HOME/miniconda3/bin:$PATH"
fi
echo ""

# ============================================================
# [3/6] 创建 conda 环境 quant (Python 3.12)
# ============================================================
echo -e "${YELLOW}[3/6] 配置 conda 环境 quant...${NC}"

if conda env list 2>/dev/null | grep -q "^quant "; then
    echo -e "  ${GREEN}环境已存在${NC}，跳过创建"
else
    echo "  创建 conda 环境 quant (Python 3.12)..."
    conda create -n quant python=3.12 -y
    echo -e "  ${GREEN}环境创建完成${NC}"
fi
echo ""

# ============================================================
# [4/6] 安装 Python 依赖
# ============================================================
echo -e "${YELLOW}[4/6] 安装 Python 依赖...${NC}"

if [ ! -f "$BACKEND_DIR/requirements.txt" ]; then
    echo -e "${RED}  找不到 backend/requirements.txt${NC}"
    exit 1
fi

# 先安装非 torch 的依赖
echo "  安装基础依赖（排除 torch）..."
grep -v "^torch" "$BACKEND_DIR/requirements.txt" | grep -v "^#" | grep -v "^$" | \
    conda run -n quant pip install -r /dev/stdin 2>&1 | tail -5

# PyTorch 特殊处理
echo "  安装 PyTorch..."
case "$GPU" in
    "Apple Silicon (MPS)")
        echo "  → macOS Apple Silicon: 安装标准 PyTorch (自动支持 MPS)"
        conda run -n quant pip install torch 2>&1 | tail -3
        # macOS 额外安装 MLX
        echo "  → 安装 MLX (Apple Silicon 专属加速框架)..."
        conda run -n quant pip install mlx-lm 2>&1 | tail -3
        ;;
    NVIDIA*)
        echo "  → NVIDIA GPU: 安装 CUDA 版 PyTorch"
        conda run -n quant pip install torch --index-url https://download.pytorch.org/whl/cu121 2>&1 | tail -3
        ;;
    *)
        if [ "$OS_NAME" = "macOS" ]; then
            echo "  → macOS (Intel): 安装标准 PyTorch"
            conda run -n quant pip install torch 2>&1 | tail -3
        else
            echo "  → 无 GPU: 安装 CPU 版 PyTorch"
            conda run -n quant pip install torch --index-url https://download.pytorch.org/whl/cpu 2>&1 | tail -3
        fi
        ;;
esac

echo -e "  ${GREEN}Python 依赖安装完成${NC}"
echo ""

# ============================================================
# [5/6] 安装 Node.js 和前端依赖
# ============================================================
echo -e "${YELLOW}[5/6] 检查 Node.js 和前端依赖...${NC}"

if command -v node &>/dev/null; then
    NODE_VER=$(node --version)
    echo -e "  ${GREEN}Node.js 已安装${NC}: $NODE_VER"
else
    echo "  未检测到 Node.js，开始安装..."
    case "$OS_NAME" in
        macOS)
            if command -v brew &>/dev/null; then
                echo "  → 使用 Homebrew 安装 Node.js..."
                brew install node
            else
                echo "  → 下载 Node.js 安装包..."
                if [ "$ARCH" = "arm64" ]; then
                    NODE_URL="https://nodejs.org/dist/v20.11.0/node-v20.11.0-darwin-arm64.tar.gz"
                else
                    NODE_URL="https://nodejs.org/dist/v20.11.0/node-v20.11.0-darwin-x64.tar.gz"
                fi
                curl -fsSL "$NODE_URL" -o /tmp/node.tar.gz
                sudo mkdir -p /usr/local/lib/nodejs
                sudo tar -xzf /tmp/node.tar.gz -C /usr/local/lib/nodejs
                NODE_DIR=$(tar -tzf /tmp/node.tar.gz | head -1 | cut -f1 -d"/")
                export PATH="/usr/local/lib/nodejs/$NODE_DIR/bin:$PATH"
                rm -f /tmp/node.tar.gz
                echo 'export PATH="/usr/local/lib/nodejs/'"$NODE_DIR"'/bin:$PATH"' >> ~/.bashrc
            fi
            ;;
        Linux)
            echo "  → 使用 NodeSource 安装 Node.js 20.x..."
            if command -v apt-get &>/dev/null; then
                curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
                sudo apt-get install -y nodejs
            elif command -v yum &>/dev/null; then
                curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
                sudo yum install -y nodejs
            else
                echo -e "${RED}  未知的包管理器，请手动安装 Node.js${NC}"
                exit 1
            fi
            ;;
    esac
    echo -e "  ${GREEN}Node.js 安装完成${NC}: $(node --version)"
fi

# 安装前端依赖
if [ -d "$FRONTEND_DIR" ]; then
    echo "  安装前端依赖 (npm install)..."
    cd "$FRONTEND_DIR"
    npm install 2>&1 | tail -5
    cd "$SCRIPT_DIR"
    echo -e "  ${GREEN}前端依赖安装完成${NC}"
else
    echo -e "${RED}  找不到 frontend/ 目录${NC}"
    exit 1
fi
echo ""

# ============================================================
# [6/6] 初始化配置
# ============================================================
echo -e "${YELLOW}[6/6] 初始化配置...${NC}"

# 复制 .env.example → .env
if [ ! -f "$BACKEND_DIR/.env" ]; then
    if [ -f "$BACKEND_DIR/.env.example" ]; then
        cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
        echo -e "  ${GREEN}已创建${NC} backend/.env （请编辑填入 API key 等配置）"
    else
        echo -e "  ${YELLOW}未找到 .env.example，跳过${NC}"
    fi
else
    echo "  backend/.env 已存在，跳过"
fi

# 创建必要目录
mkdir -p "$BACKEND_DIR/data"
mkdir -p "$BACKEND_DIR/logs"
mkdir -p "$BACKEND_DIR/finetune/adapters"
mkdir -p "$BACKEND_DIR/finetune/fused_model"
echo "  已确认 data/ logs/ finetune/ 目录存在"

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║           部署完成！                     ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  系统:  ${CYAN}$OS_NAME $ARCH${NC}"
echo -e "  GPU:   ${CYAN}$GPU${NC}"
echo -e "  Python: $(conda run -n quant python --version 2>&1)"
echo -e "  Node:  $(node --version 2>/dev/null || echo '未安装')"
echo ""
echo -e "  ${YELLOW}下一步:${NC}"
echo "  1. 编辑 backend/.env 填入 API key 等配置"
echo "  2. 运行 ${GREEN}bash start.sh${NC} 启动服务"
echo ""

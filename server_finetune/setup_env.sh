#!/usr/bin/env bash
# ============================================================
# setup_env.sh — 服务器一键安装训练环境（NVIDIA GPU）
# 用法: bash setup_env.sh
# ============================================================
set -euo pipefail

ENV_NAME="finetune"

echo "========================================"
echo "  服务器 Fine-Tune 环境安装"
echo "========================================"

# 彻底清除代理（服务器直连外网）
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY
export http_proxy="" https_proxy="" HTTP_PROXY="" HTTPS_PROXY="" all_proxy="" ALL_PROXY="" no_proxy="" NO_PROXY=""
echo "[INFO] 已清除代理环境变量"

# 检查 conda
if ! command -v conda &> /dev/null; then
    echo "[ERROR] conda 未安装，请先安装 Miniconda/Anaconda"
    exit 1
fi

# 初始化 conda（让 conda activate 可用）
eval "$(conda shell.bash hook 2>/dev/null)"

# 检查 NVIDIA GPU
if command -v nvidia-smi &> /dev/null; then
    echo "[INFO] GPU 信息:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
    echo "[WARN] 未检测到 nvidia-smi，确保已安装 NVIDIA 驱动"
fi

# 创建 conda 环境
if conda env list | grep -q "^${ENV_NAME} "; then
    echo "[INFO] conda 环境 '${ENV_NAME}' 已存在，跳过创建"
else
    echo "[INFO] 创建 conda 环境 '${ENV_NAME}' (Python 3.11)..."
    conda create -n "${ENV_NAME}" python=3.11 -y
fi

# 激活环境（不用 conda run，避免重新加载 .bashrc 里的代理）
conda activate "${ENV_NAME}"

echo "[INFO] 安装 PyTorch (CUDA 12.8 — 支持 RTX 5070 Blackwell)..."
pip install torch --index-url https://download.pytorch.org/whl/cu128

echo "[INFO] 安装 Unsloth + 训练依赖..."
pip install \
    unsloth \
    "transformers>=4.51.0" \
    datasets \
    peft \
    accelerate \
    bitsandbytes \
    trl \
    loguru \
    tqdm

echo ""
echo "========================================"
echo "  安装完成！"
echo "========================================"
echo ""
echo "后续步骤:"
echo "  1. python merge_data.py          # 合并数据"
echo "  2. python train.py --iters 5000  # 开始训练"
echo ""

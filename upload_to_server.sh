#!/usr/bin/env bash
# ============================================================
# upload_to_server.sh — 上传训练脚本和数据到远程服务器
# 用法: bash upload_to_server.sh [user@host]
# ============================================================
set -euo pipefail

# 从 .env 读取配置（或用命令行参数覆盖）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/backend/.env"

if [ -f "$ENV_FILE" ]; then
    SSH_HOST=$(grep -E '^FINETUNE_SSH_HOST=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    SSH_USER=$(grep -E '^FINETUNE_SSH_USER=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    SSH_PORT=$(grep -E '^FINETUNE_SSH_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    SSH_KEY=$(grep -E '^FINETUNE_SSH_KEY=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    REMOTE_DIR=$(grep -E '^FINETUNE_REMOTE_DIR=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
fi

# 默认值
SSH_HOST="${SSH_HOST:-}"
SSH_USER="${SSH_USER:-}"
SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_rsa}"
REMOTE_DIR="${REMOTE_DIR:-/home/${SSH_USER}/server_finetune}"

# 命令行覆盖
if [ $# -ge 1 ]; then
    # user@host 格式
    SSH_USER="${1%%@*}"
    SSH_HOST="${1##*@}"
fi

if [ -z "$SSH_HOST" ] || [ -z "$SSH_USER" ]; then
    echo "用法: bash upload_to_server.sh [user@host]"
    echo "或在 backend/.env 中配置 FINETUNE_SSH_HOST / FINETUNE_SSH_USER"
    exit 1
fi

SSH_DEST="${SSH_USER}@${SSH_HOST}"
SSH_OPTS="-p ${SSH_PORT}"
if [ -f "$(eval echo "$SSH_KEY")" ]; then
    SSH_OPTS="${SSH_OPTS} -i $(eval echo "$SSH_KEY")"
fi

echo "========================================"
echo "  上传到服务器: ${SSH_DEST}"
echo "  远程目录:     ${REMOTE_DIR}"
echo "========================================"

# 1. 创建远程目录
echo ""
echo "[1/4] 创建远程目录..."
ssh ${SSH_OPTS} "${SSH_DEST}" "mkdir -p ${REMOTE_DIR}/data/finance ${REMOTE_DIR}/data/strategy ${REMOTE_DIR}/data/merged ${REMOTE_DIR}/output"

# 2. 上传训练脚本
echo ""
echo "[2/4] 上传训练脚本..."
rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    "${SCRIPT_DIR}/server_finetune/setup_env.sh" \
    "${SCRIPT_DIR}/server_finetune/train.py" \
    "${SCRIPT_DIR}/server_finetune/merge_data.py" \
    "${SCRIPT_DIR}/server_finetune/export_model.py" \
    "${SSH_DEST}:${REMOTE_DIR}/"

# 3. 上传金融通识数据
echo ""
echo "[3/4] 上传金融通识数据..."
FINANCE_DIR="${SCRIPT_DIR}/datasets/merged"
if [ -d "$FINANCE_DIR" ] && [ "$(ls -A "$FINANCE_DIR"/*.jsonl 2>/dev/null)" ]; then
    rsync -avz --progress \
        -e "ssh ${SSH_OPTS}" \
        "${FINANCE_DIR}/" \
        "${SSH_DEST}:${REMOTE_DIR}/data/finance/"
    echo "  金融通识数据上传完成"
else
    echo "  [跳过] 金融通识数据目录不存在或为空: ${FINANCE_DIR}"
fi

# 4. 上传策略代码数据
echo ""
echo "[4/4] 上传策略代码数据..."
STRATEGY_DIR="${SCRIPT_DIR}/backend/finetune/data/raw"
if [ -d "$STRATEGY_DIR" ] && [ "$(ls -A "$STRATEGY_DIR"/*.jsonl 2>/dev/null)" ]; then
    rsync -avz --progress \
        -e "ssh ${SSH_OPTS}" \
        "${STRATEGY_DIR}/" \
        "${SSH_DEST}:${REMOTE_DIR}/data/strategy/"
    echo "  策略代码数据上传完成"
else
    echo "  [跳过] 策略代码数据目录不存在或为空: ${STRATEGY_DIR}"
fi

echo ""
echo "========================================"
echo "  上传完成！"
echo "========================================"
echo ""
echo "后续步骤（SSH 到服务器执行）:"
echo "  ssh ${SSH_OPTS} ${SSH_DEST}"
echo "  cd ${REMOTE_DIR}"
echo "  bash setup_env.sh         # 首次安装环境"
echo "  python merge_data.py      # 合并数据"
echo ""
echo "然后在前端切换到 Remote 模式开始训练"

#!/usr/bin/env bash
# ============================================================
# download_from_server.sh — 从远程服务器下载训练好的模型
# 用法: bash download_from_server.sh [user@host]
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/backend/.env"

if [ -f "$ENV_FILE" ]; then
    SSH_HOST=$(grep -E '^FINETUNE_SSH_HOST=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    SSH_USER=$(grep -E '^FINETUNE_SSH_USER=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    SSH_PORT=$(grep -E '^FINETUNE_SSH_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    SSH_KEY=$(grep -E '^FINETUNE_SSH_KEY=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
    REMOTE_DIR=$(grep -E '^FINETUNE_REMOTE_DIR=' "$ENV_FILE" | cut -d= -f2 | tr -d ' ')
fi

SSH_HOST="${SSH_HOST:-}"
SSH_USER="${SSH_USER:-}"
SSH_PORT="${SSH_PORT:-22}"
SSH_KEY="${SSH_KEY:-~/.ssh/id_rsa}"
REMOTE_DIR="${REMOTE_DIR:-/home/${SSH_USER}/server_finetune}"

if [ $# -ge 1 ]; then
    SSH_USER="${1%%@*}"
    SSH_HOST="${1##*@}"
fi

if [ -z "$SSH_HOST" ] || [ -z "$SSH_USER" ]; then
    echo "用法: bash download_from_server.sh [user@host]"
    echo "或在 backend/.env 中配置 FINETUNE_SSH_HOST / FINETUNE_SSH_USER"
    exit 1
fi

SSH_DEST="${SSH_USER}@${SSH_HOST}"
SSH_OPTS="-p ${SSH_PORT}"
if [ -f "$(eval echo "$SSH_KEY")" ]; then
    SSH_OPTS="${SSH_OPTS} -i $(eval echo "$SSH_KEY")"
fi

LOCAL_OUTPUT="${SCRIPT_DIR}/backend/finetune/remote_output"
mkdir -p "${LOCAL_OUTPUT}"

echo "========================================"
echo "  从服务器下载模型: ${SSH_DEST}"
echo "  远程目录:         ${REMOTE_DIR}/output"
echo "  本地目录:         ${LOCAL_OUTPUT}"
echo "========================================"

# 检查远程有哪些输出
echo ""
echo "[INFO] 检查远程输出文件..."
ssh ${SSH_OPTS} "${SSH_DEST}" "ls -lhR ${REMOTE_DIR}/output/ 2>/dev/null || echo '(空)'"

# 下载 adapter
echo ""
echo "[1/2] 下载 Adapter..."
rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    "${SSH_DEST}:${REMOTE_DIR}/output/adapter/" \
    "${LOCAL_OUTPUT}/adapter/" \
    2>/dev/null || echo "  [跳过] Adapter 目录不存在"

# 下载 GGUF
echo ""
echo "[2/2] 下载 GGUF..."
rsync -avz --progress \
    -e "ssh ${SSH_OPTS}" \
    "${SSH_DEST}:${REMOTE_DIR}/output/gguf/" \
    "${LOCAL_OUTPUT}/gguf/" \
    2>/dev/null || echo "  [跳过] GGUF 目录不存在"

echo ""
echo "========================================"
echo "  下载完成！"
echo "========================================"
echo ""
echo "文件位置: ${LOCAL_OUTPUT}"
echo ""

# 提示 GGUF 用法
if [ -d "${LOCAL_OUTPUT}/gguf" ] && ls "${LOCAL_OUTPUT}/gguf/"*.gguf &>/dev/null; then
    GGUF_FILE=$(ls "${LOCAL_OUTPUT}/gguf/"*.gguf | head -1)
    echo "Ollama 部署方法:"
    echo "  1. 创建 Modelfile:"
    echo "     FROM ${GGUF_FILE}"
    echo "  2. ollama create my-finetuned-qwen -f Modelfile"
    echo "  3. ollama run my-finetuned-qwen"
fi

#!/bin/bash
# MLX 本地模型服务器启动脚本
# 使用 Apple MLX 框架在 Apple Silicon 上运行 LLM

MODEL="${ALPHA_LAB_LOCAL_MODEL:-mlx-community/Qwen2.5-Coder-14B-Instruct-4bit}"
PORT="${MLX_PORT:-11434}"
HOST="${MLX_HOST:-0.0.0.0}"

echo "Starting MLX Server..."
echo "  Model: $MODEL"
echo "  Address: $HOST:$PORT"

conda run -n quant python -m mlx_lm server \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --max-tokens 4000

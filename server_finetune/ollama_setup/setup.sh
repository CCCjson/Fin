#!/bin/bash
# ==========================================
# 在服务器上创建 fin-qwen:7b 专用模型
# ==========================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Step 1: 拉取基础模型 qwen3:8b ==="
ollama pull qwen3:8b

echo ""
echo "=== Step 2: 创建 fin-qwen:7b ==="
ollama create fin-qwen:7b -f "$SCRIPT_DIR/Modelfile"

echo ""
echo "=== Step 3: 验证 ==="
ollama list

echo ""
echo "=== Step 4: 快速测试 ==="
echo '请用MACD金叉策略写一个简单的买入信号' | ollama run fin-qwen:7b --nowordwrap 2>/dev/null | head -30

echo ""
echo "=== 完成！fin-qwen:7b 已就绪 ==="
echo "后续微调完成后，用 GGUF 替换 Modelfile 中的 FROM 即可升级为微调版"

#!/bin/bash
# ============================================================
#  Fin 量化交易系统 — 导出模型 (macOS / Linux)
#  打包 LoRA adapters + 融合模型 + 元信息 → zip
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FINETUNE_DIR="$SCRIPT_DIR/backend/finetune"
ADAPTERS_DIR="$FINETUNE_DIR/adapters"
FUSED_DIR="$FINETUNE_DIR/fused_model"
DATA_DIR="$FINETUNE_DIR/data/final"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}=== Fin 量化交易系统 — 导出模型 ===${NC}"
echo ""

# 检查是否有可导出的内容
HAS_ADAPTERS=false
HAS_FUSED=false
HAS_DATA=false

if [ -d "$ADAPTERS_DIR" ] && ls "$ADAPTERS_DIR"/*.safetensors >/dev/null 2>&1; then
    HAS_ADAPTERS=true
    ADAPTER_SIZE=$(du -sh "$ADAPTERS_DIR" 2>/dev/null | cut -f1)
    echo -e "  ${GREEN}✓${NC} adapters/          ($ADAPTER_SIZE)"
fi

if [ -d "$ADAPTERS_DIR" ] && ls "$ADAPTERS_DIR"/*.json >/dev/null 2>&1; then
    HAS_ADAPTERS=true
fi

if [ -d "$ADAPTERS_DIR" ] && ls "$ADAPTERS_DIR"/*.yaml >/dev/null 2>&1; then
    HAS_ADAPTERS=true
fi

if [ -d "$FUSED_DIR" ] && [ "$(ls -A "$FUSED_DIR" 2>/dev/null | grep -v '.gitkeep')" ]; then
    HAS_FUSED=true
    FUSED_SIZE=$(du -sh "$FUSED_DIR" 2>/dev/null | cut -f1)
    echo -e "  ${GREEN}✓${NC} fused_model/       ($FUSED_SIZE)"
fi

if [ -d "$DATA_DIR" ] && ls "$DATA_DIR"/*.jsonl >/dev/null 2>&1; then
    HAS_DATA=true
    DATA_SIZE=$(du -sh "$DATA_DIR" 2>/dev/null | cut -f1)
    echo -e "  ${YELLOW}?${NC} data/final/        ($DATA_SIZE) [可选]"
fi

if [ "$HAS_ADAPTERS" = false ] && [ "$HAS_FUSED" = false ]; then
    echo -e "${RED}  未找到可导出的模型文件${NC}"
    echo "  请确认以下目录有模型文件:"
    echo "    $ADAPTERS_DIR/"
    echo "    $FUSED_DIR/"
    exit 1
fi

# 询问是否包含训练数据
INCLUDE_DATA=false
if [ "$HAS_DATA" = true ]; then
    echo ""
    read -p "  是否包含训练数据？(y/N) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        INCLUDE_DATA=true
    fi
fi

# 生成时间戳和文件名
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
ZIP_NAME="fin_model_export_${TIMESTAMP}.zip"
ZIP_PATH="$SCRIPT_DIR/$ZIP_NAME"
TEMP_DIR=$(mktemp -d)
EXPORT_DIR="$TEMP_DIR/fin_model_export"
mkdir -p "$EXPORT_DIR"

echo ""
echo -e "${YELLOW}打包中...${NC}"

# 复制 adapters
if [ "$HAS_ADAPTERS" = true ]; then
    mkdir -p "$EXPORT_DIR/adapters"
    # 复制所有非 .gitkeep 文件
    find "$ADAPTERS_DIR" -maxdepth 1 -type f ! -name ".gitkeep" -exec cp {} "$EXPORT_DIR/adapters/" \;
    echo "  + adapters/"
fi

# 复制 fused_model
if [ "$HAS_FUSED" = true ]; then
    mkdir -p "$EXPORT_DIR/fused_model"
    find "$FUSED_DIR" -maxdepth 1 -type f ! -name ".gitkeep" -exec cp {} "$EXPORT_DIR/fused_model/" \;
    echo "  + fused_model/"
fi

# 复制训练数据
if [ "$INCLUDE_DATA" = true ]; then
    mkdir -p "$EXPORT_DIR/data/final"
    cp "$DATA_DIR"/*.jsonl "$EXPORT_DIR/data/final/" 2>/dev/null || true
    echo "  + data/final/"
fi

# 生成 metadata.json
OS_NAME="$(uname -s)"
case "$OS_NAME" in
    Darwin) OS_DISPLAY="macOS" ;;
    Linux)  OS_DISPLAY="Linux" ;;
    *)      OS_DISPLAY="$OS_NAME" ;;
esac

# 检测 GPU
GPU_DISPLAY="CPU"
if [ "$OS_DISPLAY" = "macOS" ] && [ "$(uname -m)" = "arm64" ]; then
    GPU_DISPLAY="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo 'Apple Silicon')"
elif command -v nvidia-smi &>/dev/null; then
    GPU_DISPLAY="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
fi

# 读取训练迭代次数（从训练配置）
TRAIN_ITERS="unknown"
if [ -f "$ADAPTERS_DIR/lora_train_config.yaml" ]; then
    TRAIN_ITERS=$(grep -E "^iters:|^  iters:" "$ADAPTERS_DIR/lora_train_config.yaml" 2>/dev/null | head -1 | sed 's/.*: *//' || echo "unknown")
fi

# 读取 base model
BASE_MODEL="unknown"
if [ -f "$ADAPTERS_DIR/adapter_config.json" ]; then
    BASE_MODEL=$(python3 -c "import json; print(json.load(open('$ADAPTERS_DIR/adapter_config.json')).get('base_model_name_or_path', 'unknown'))" 2>/dev/null || echo "unknown")
fi

cat > "$EXPORT_DIR/metadata.json" << METAEOF
{
  "export_date": "$(date +%Y-%m-%d)",
  "export_timestamp": "$TIMESTAMP",
  "machine": "$(hostname)",
  "os": "$OS_DISPLAY",
  "arch": "$(uname -m)",
  "gpu": "$GPU_DISPLAY",
  "base_model": "$BASE_MODEL",
  "training_iters": "$TRAIN_ITERS",
  "includes_data": $INCLUDE_DATA,
  "includes_adapters": $HAS_ADAPTERS,
  "includes_fused_model": $HAS_FUSED
}
METAEOF
echo "  + metadata.json"

# 打包 zip
cd "$TEMP_DIR"
zip -r "$ZIP_PATH" "fin_model_export/" -x "*.DS_Store" > /dev/null
cd "$SCRIPT_DIR"

# 清理临时目录
rm -rf "$TEMP_DIR"

# 输出结果
ZIP_SIZE=$(du -h "$ZIP_PATH" | cut -f1)
echo ""
echo -e "${GREEN}${BOLD}导出完成！${NC}"
echo ""
echo -e "  文件: ${CYAN}$ZIP_PATH${NC}"
echo -e "  大小: ${CYAN}$ZIP_SIZE${NC}"
echo ""
echo -e "  ${YELLOW}内容:${NC}"
[ "$HAS_ADAPTERS" = true ] && echo "    adapters/          (LoRA 权重 + 配置)"
[ "$HAS_FUSED" = true ]    && echo "    fused_model/       (融合后完整模型)"
[ "$INCLUDE_DATA" = true ] && echo "    data/final/        (训练数据)"
echo "    metadata.json      (导出元信息)"
echo ""
echo -e "  ${YELLOW}在目标机器上导入:${NC}"
echo "    bash import_model.sh $ZIP_NAME"
echo ""

#!/bin/bash
# ============================================================
#  Fin 量化交易系统 — 导入模型 (macOS / Linux)
#  从 zip 解压 LoRA adapters + 融合模型到正确位置
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FINETUNE_DIR="$SCRIPT_DIR/backend/finetune"
ADAPTERS_DIR="$FINETUNE_DIR/adapters"
FUSED_DIR="$FINETUNE_DIR/fused_model"

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}=== Fin 量化交易系统 — 导入模型 ===${NC}"
echo ""

# 检查参数
if [ -z "$1" ]; then
    echo -e "${RED}用法: bash import_model.sh <zip文件路径>${NC}"
    echo ""
    echo "  示例:"
    echo "    bash import_model.sh fin_model_export_20260302_143000.zip"
    echo "    bash import_model.sh /path/to/model.zip"
    exit 1
fi

ZIP_FILE="$1"

# 支持相对路径
if [[ ! "$ZIP_FILE" = /* ]]; then
    ZIP_FILE="$SCRIPT_DIR/$ZIP_FILE"
fi

if [ ! -f "$ZIP_FILE" ]; then
    echo -e "${RED}文件不存在: $ZIP_FILE${NC}"
    exit 1
fi

ZIP_SIZE=$(du -h "$ZIP_FILE" | cut -f1)
echo -e "  导入文件: ${CYAN}$(basename "$ZIP_FILE")${NC} ($ZIP_SIZE)"
echo ""

# ============================================================
# [1/3] 解压 zip
# ============================================================
echo -e "${YELLOW}[1/3] 解压文件...${NC}"

TEMP_DIR=$(mktemp -d)
unzip -q "$ZIP_FILE" -d "$TEMP_DIR"

# 查找解压后的根目录（可能在子目录中）
EXPORT_ROOT=""
if [ -d "$TEMP_DIR/fin_model_export" ]; then
    EXPORT_ROOT="$TEMP_DIR/fin_model_export"
elif [ -f "$TEMP_DIR/metadata.json" ]; then
    EXPORT_ROOT="$TEMP_DIR"
else
    # 查找包含 metadata.json 的目录
    FOUND=$(find "$TEMP_DIR" -name "metadata.json" -maxdepth 2 | head -1)
    if [ -n "$FOUND" ]; then
        EXPORT_ROOT="$(dirname "$FOUND")"
    else
        echo -e "${RED}  无法识别 zip 内容（缺少 metadata.json）${NC}"
        rm -rf "$TEMP_DIR"
        exit 1
    fi
fi

echo "  解压完成"

# 显示 metadata
if [ -f "$EXPORT_ROOT/metadata.json" ]; then
    echo ""
    echo -e "${BOLD}  导出信息:${NC}"
    python3 -c "
import json, sys
m = json.load(open('$EXPORT_ROOT/metadata.json'))
print(f\"    日期:   {m.get('export_date', 'N/A')}\")
print(f\"    机器:   {m.get('machine', 'N/A')}\")
print(f\"    系统:   {m.get('os', 'N/A')} {m.get('arch', '')}\")
print(f\"    GPU:    {m.get('gpu', 'N/A')}\")
print(f\"    模型:   {m.get('base_model', 'N/A')}\")
print(f\"    迭代:   {m.get('training_iters', 'N/A')}\")
" 2>/dev/null || cat "$EXPORT_ROOT/metadata.json"
fi
echo ""

# ============================================================
# [2/3] 复制文件到目标位置
# ============================================================
echo -e "${YELLOW}[2/3] 导入模型文件...${NC}"

IMPORTED=0

# 导入 adapters
if [ -d "$EXPORT_ROOT/adapters" ] && [ "$(ls -A "$EXPORT_ROOT/adapters")" ]; then
    mkdir -p "$ADAPTERS_DIR"
    # 备份现有的（如果有）
    if ls "$ADAPTERS_DIR"/*.safetensors >/dev/null 2>&1; then
        BACKUP_DIR="$ADAPTERS_DIR.backup_$(date +%Y%m%d_%H%M%S)"
        echo -e "  ${YELLOW}备份现有 adapters → $(basename "$BACKUP_DIR")${NC}"
        cp -r "$ADAPTERS_DIR" "$BACKUP_DIR"
    fi
    cp -f "$EXPORT_ROOT/adapters/"* "$ADAPTERS_DIR/"
    ADAPTER_COUNT=$(ls "$EXPORT_ROOT/adapters/" | wc -l | tr -d ' ')
    echo -e "  ${GREEN}✓${NC} adapters/          ($ADAPTER_COUNT 个文件)"
    IMPORTED=$((IMPORTED + 1))
fi

# 导入 fused_model
if [ -d "$EXPORT_ROOT/fused_model" ] && [ "$(ls -A "$EXPORT_ROOT/fused_model")" ]; then
    mkdir -p "$FUSED_DIR"
    # 备份现有的
    if [ "$(ls -A "$FUSED_DIR" 2>/dev/null | grep -v '.gitkeep')" ]; then
        BACKUP_DIR="$FUSED_DIR.backup_$(date +%Y%m%d_%H%M%S)"
        echo -e "  ${YELLOW}备份现有 fused_model → $(basename "$BACKUP_DIR")${NC}"
        cp -r "$FUSED_DIR" "$BACKUP_DIR"
    fi
    cp -f "$EXPORT_ROOT/fused_model/"* "$FUSED_DIR/"
    FUSED_COUNT=$(ls "$EXPORT_ROOT/fused_model/" | wc -l | tr -d ' ')
    echo -e "  ${GREEN}✓${NC} fused_model/       ($FUSED_COUNT 个文件)"
    IMPORTED=$((IMPORTED + 1))
fi

# 导入训练数据（如果有）
if [ -d "$EXPORT_ROOT/data/final" ] && [ "$(ls -A "$EXPORT_ROOT/data/final")" ]; then
    DATA_TARGET="$FINETUNE_DIR/data/final"
    mkdir -p "$DATA_TARGET"
    cp -f "$EXPORT_ROOT/data/final/"* "$DATA_TARGET/"
    DATA_COUNT=$(ls "$EXPORT_ROOT/data/final/" | wc -l | tr -d ' ')
    echo -e "  ${GREEN}✓${NC} data/final/        ($DATA_COUNT 个文件)"
    IMPORTED=$((IMPORTED + 1))
fi

# ============================================================
# [3/3] 清理并打印结果
# ============================================================
echo ""
echo -e "${YELLOW}[3/3] 清理临时文件...${NC}"
rm -rf "$TEMP_DIR"

echo ""
if [ $IMPORTED -gt 0 ]; then
    echo -e "${GREEN}${BOLD}导入完成！${NC} (共 $IMPORTED 个模块)"
    echo ""
    echo -e "  模型位置:"
    [ -d "$ADAPTERS_DIR" ] && echo "    adapters:    $ADAPTERS_DIR"
    [ -d "$FUSED_DIR" ]    && echo "    fused_model: $FUSED_DIR"
    echo ""
    echo -e "  ${YELLOW}下一步:${NC}"
    echo "    如需将 adapters 融合到基础模型，运行:"
    echo "    conda run -n quant python backend/finetune/deploy.py"
else
    echo -e "${RED}未导入任何文件${NC}"
fi
echo ""

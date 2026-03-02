#!/usr/bin/env python3
"""
合并金融通识数据 + Alpha Lab 策略代码数据

用法:
    python merge_data.py
    python merge_data.py --finance_dir data/finance --strategy_dir data/strategy
    python merge_data.py --split 0.8 0.1 0.1
"""
import argparse
import json
import random
from pathlib import Path

BASE_DIR = Path(__file__).parent
DEFAULT_FINANCE_DIR = BASE_DIR / "data" / "finance"
DEFAULT_STRATEGY_DIR = BASE_DIR / "data" / "strategy"
OUTPUT_DIR = BASE_DIR / "data" / "merged"


def load_jsonl(path: Path) -> list:
    """加载单个 JSONL 文件"""
    items = []
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  [WARN] {path.name} 第 {i+1} 行解析失败，跳过")
    return items


def load_all_jsonl(directory: Path) -> list:
    """递归加载目录下所有 JSONL 文件"""
    items = []
    if not directory.exists():
        print(f"  [WARN] 目录不存在: {directory}")
        return items

    for path in sorted(directory.rglob("*.jsonl")):
        data = load_jsonl(path)
        print(f"  {path.relative_to(directory)}: {len(data)} 条")
        items.extend(data)
    return items


def validate_item(item: dict) -> bool:
    """验证单条数据格式"""
    if "messages" not in item:
        return False
    messages = item["messages"]
    if not isinstance(messages, list) or len(messages) < 2:
        return False
    roles = [m.get("role") for m in messages]
    if "user" not in roles or "assistant" not in roles:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="合并训练数据")
    parser.add_argument("--finance_dir", type=str, default=str(DEFAULT_FINANCE_DIR))
    parser.add_argument("--strategy_dir", type=str, default=str(DEFAULT_STRATEGY_DIR))
    parser.add_argument("--output_dir", type=str, default=str(OUTPUT_DIR))
    parser.add_argument("--split", nargs=3, type=float, default=[0.8, 0.1, 0.1],
                        help="train/valid/test 比例")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    finance_dir = Path(args.finance_dir)
    strategy_dir = Path(args.strategy_dir)
    output_dir = Path(args.output_dir)

    print("=" * 50)
    print("  数据合并")
    print("=" * 50)

    # 加载数据
    print(f"\n[1/4] 加载金融通识数据: {finance_dir}")
    finance_data = load_all_jsonl(finance_dir)

    print(f"\n[2/4] 加载策略代码数据: {strategy_dir}")
    strategy_data = load_all_jsonl(strategy_dir)

    # 合并
    all_data = finance_data + strategy_data
    print(f"\n[3/4] 合并数据:")
    print(f"  金融通识: {len(finance_data)} 条")
    print(f"  策略代码: {len(strategy_data)} 条")
    print(f"  合计:     {len(all_data)} 条")

    # 验证
    valid_data = [item for item in all_data if validate_item(item)]
    invalid_count = len(all_data) - len(valid_data)
    if invalid_count > 0:
        print(f"  [WARN] 过滤无效数据: {invalid_count} 条")
    print(f"  有效数据: {len(valid_data)} 条")

    if len(valid_data) == 0:
        print("\n[ERROR] 没有有效数据，退出")
        return

    # 打乱 & 分割
    random.seed(args.seed)
    random.shuffle(valid_data)

    train_ratio, valid_ratio, test_ratio = args.split
    n = len(valid_data)
    n_train = int(n * train_ratio)
    n_valid = int(n * valid_ratio)

    train_data = valid_data[:n_train]
    valid_split = valid_data[n_train:n_train + n_valid]
    test_data = valid_data[n_train + n_valid:]

    print(f"\n[4/4] 数据分割 (seed={args.seed}):")
    print(f"  Train: {len(train_data)} 条 ({train_ratio*100:.0f}%)")
    print(f"  Valid: {len(valid_split)} 条 ({valid_ratio*100:.0f}%)")
    print(f"  Test:  {len(test_data)} 条 ({test_ratio*100:.0f}%)")

    # 保存
    output_dir.mkdir(parents=True, exist_ok=True)

    for split_name, split_data in [("train", train_data), ("valid", valid_split), ("test", test_data)]:
        path = output_dir / f"{split_name}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for item in split_data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"  -> {path}: {len(split_data)} 条")

    print(f"\n合并完成！")
    print(f"输出目录: {output_dir}")


if __name__ == "__main__":
    main()

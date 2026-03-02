#!/usr/bin/env python3
"""
Unsloth QLoRA 训练 — NDJSON 事件输出（兼容前端 FineTune.tsx）

在 NVIDIA GPU 服务器上运行，stdout 输出 NDJSON 事件，
本地后端通过 SSH 读取 stdout 透传给前端。

用法:
    python train.py --iters 5000 --lr 2e-4 --batch_size 4
    python train.py --resume  # 从 checkpoint 继续
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# ==================== NDJSON 事件输出 ====================

def emit(event: str, **data):
    """输出一条 NDJSON 事件到 stdout"""
    line = json.dumps({"event": event, **data}, ensure_ascii=False)
    print(line, flush=True)


def emit_log(message: str):
    """输出日志事件"""
    emit("log", line=message)


# ==================== 路径 ====================

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data" / "merged"
OUTPUT_DIR = BASE_DIR / "output"
CHECKPOINT_DIR = OUTPUT_DIR / "checkpoints"

# ==================== 默认参数（RTX 5070 12GB 优化）====================

DEFAULTS = {
    "model": "unsloth/Qwen2.5-7B-Instruct-bnb-4bit",
    "max_seq_length": 1024,
    "lora_r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.0,
    "target_modules": [
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    "batch_size": 1,
    "grad_accumulation": 16,
    "learning_rate": 2e-4,
    "num_epochs": 1,
    "max_steps": -1,  # -1 = 由 num_epochs 决定; >0 时覆盖 epochs
    "warmup_ratio": 0.03,
    "lr_scheduler": "cosine",
    "logging_steps": 10,
    "eval_steps": 200,
    "save_steps": 500,
    "weight_decay": 0.01,
    "seed": 42,
}


# ==================== HuggingFace Trainer Callback ====================

def make_ndjson_callback(total_steps: int):
    """创建输出 NDJSON 事件的 TrainerCallback"""
    from transformers import TrainerCallback

    class NDJSONCallback(TrainerCallback):
        def __init__(self):
            self._last_log_step = -1

        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs is None:
                return
            step = state.global_step
            if step == self._last_log_step:
                return
            self._last_log_step = step

            # 训练 loss
            if "loss" in logs:
                emit(
                    "train_step",
                    iter=step,
                    train_loss=round(logs["loss"], 6),
                    learning_rate=logs.get("learning_rate", 0),
                    it_sec=round(1.0 / logs["train_steps_per_second"], 3) if logs.get("train_steps_per_second") else None,
                )

            # 验证 loss
            if "eval_loss" in logs:
                emit("val_step", iter=step, val_loss=round(logs["eval_loss"], 6))

        def on_save(self, args, state, control, **kwargs):
            emit_log(f"Checkpoint saved at step {state.global_step}")

    return NDJSONCallback()


# ==================== 数据加载 ====================

def load_dataset_from_jsonl(split: str):
    """加载 JSONL 数据集"""
    from datasets import load_dataset

    path = DATA_DIR / f"{split}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"数据文件不存在: {path}")

    ds = load_dataset("json", data_files=str(path), split="train")
    emit_log(f"加载 {split} 数据: {len(ds)} 条")
    return ds


def format_for_chat(example):
    """将 messages 格式转为 Unsloth chat 模板所需格式"""
    # 数据已经是 {"messages": [{"role": ..., "content": ...}]} 格式
    # Unsloth 的 chat 模板会自动处理
    return example


# ==================== 主训练流程 ====================

def main():
    parser = argparse.ArgumentParser(description="Unsloth QLoRA 训练（NDJSON 事件输出）")
    parser.add_argument("--model", type=str, default=DEFAULTS["model"], help="模型名称")
    parser.add_argument("--iters", type=int, default=DEFAULTS["max_steps"], help="最大训练步数 (-1=自动)")
    parser.add_argument("--lr", type=float, default=DEFAULTS["learning_rate"], help="学习率")
    parser.add_argument("--batch_size", type=int, default=DEFAULTS["batch_size"], help="Batch size")
    parser.add_argument("--grad_accumulation", type=int, default=DEFAULTS["grad_accumulation"])
    parser.add_argument("--epochs", type=int, default=DEFAULTS["num_epochs"], help="训练 epochs")
    parser.add_argument("--max_seq_length", type=int, default=DEFAULTS["max_seq_length"])
    parser.add_argument("--lora_r", type=int, default=DEFAULTS["lora_r"])
    parser.add_argument("--lora_alpha", type=int, default=DEFAULTS["lora_alpha"])
    parser.add_argument("--eval_steps", type=int, default=DEFAULTS["eval_steps"])
    parser.add_argument("--save_steps", type=int, default=DEFAULTS["save_steps"])
    parser.add_argument("--logging_steps", type=int, default=DEFAULTS["logging_steps"])
    parser.add_argument("--resume", action="store_true", help="从 checkpoint 继续训练")
    parser.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    args = parser.parse_args()

    # ---- 1) Pipeline 事件 ----
    emit("pipeline_start", stages=["train"])
    emit("stage_start", stage="train", stage_index=0, total_stages=1)

    try:
        # ---- 2) 检查数据 ----
        for split in ("train", "valid"):
            p = DATA_DIR / f"{split}.jsonl"
            if not p.exists():
                emit("train_complete", success=False, error=f"数据文件缺失: {p}")
                emit("pipeline_error", stage="train", error=f"数据文件缺失: {p}")
                return

        # ---- 3) 加载模型 ----
        emit_log(f"加载模型: {args.model}")
        emit_log(f"LoRA r={args.lora_r}, alpha={args.lora_alpha}")

        from unsloth import FastLanguageModel

        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=args.model,
            max_seq_length=args.max_seq_length,
            dtype=None,
            load_in_4bit=True,
        )

        emit_log("模型加载完成")

        # ---- 4) 配置 LoRA ----
        model = FastLanguageModel.get_peft_model(
            model,
            r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=DEFAULTS["lora_dropout"],
            target_modules=DEFAULTS["target_modules"],
            bias="none",
            use_gradient_checkpointing="unsloth",
            random_state=args.seed,
        )

        emit_log("LoRA 适配器已配置")

        # ---- 5) 加载数据 ----
        train_ds = load_dataset_from_jsonl("train")
        valid_ds = load_dataset_from_jsonl("valid")

        # ---- 6) 应用 chat 模板 ----
        from unsloth.chat_templates import get_chat_template

        tokenizer = get_chat_template(
            tokenizer,
            chat_template="qwen-2.5",
        )

        # 数据是 {"messages": [...]} 格式，需要转成 "conversations" 给 Unsloth
        def rename_messages(example):
            if "messages" in example and "conversations" not in example:
                example["conversations"] = example["messages"]
            return example

        train_ds = train_ds.map(rename_messages)
        valid_ds = valid_ds.map(rename_messages)

        from unsloth.chat_templates import standardize_sharegpt

        train_ds = standardize_sharegpt(train_ds)
        valid_ds = standardize_sharegpt(valid_ds)

        def apply_template(examples):
            texts = [
                tokenizer.apply_chat_template(
                    convo, tokenize=False, add_generation_prompt=False,
                )
                for convo in examples["conversations"]
            ]
            return {"text": texts}

        train_ds = train_ds.map(apply_template, batched=True)
        valid_ds = valid_ds.map(apply_template, batched=True)

        emit_log("Chat 模板已应用")

        # ---- 7) 计算总步数 ----
        effective_batch = args.batch_size * args.grad_accumulation
        steps_per_epoch = max(1, len(train_ds) // effective_batch)

        if args.iters > 0:
            total_steps = args.iters
        else:
            total_steps = steps_per_epoch * args.epochs

        emit_log(f"总步数: {total_steps} (数据 {len(train_ds)} 条, "
                 f"effective batch {effective_batch}, "
                 f"每 epoch {steps_per_epoch} 步)")

        # ---- 8) 训练配置 ----
        from trl import SFTTrainer
        from transformers import TrainingArguments

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)

        training_args = TrainingArguments(
            output_dir=str(CHECKPOINT_DIR),
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accumulation,
            num_train_epochs=args.epochs if args.iters <= 0 else 9999,
            max_steps=total_steps if args.iters > 0 else -1,
            learning_rate=args.lr,
            lr_scheduler_type=DEFAULTS["lr_scheduler"],
            warmup_ratio=DEFAULTS["warmup_ratio"],
            weight_decay=DEFAULTS["weight_decay"],
            logging_steps=args.logging_steps,
            eval_strategy="steps",
            eval_steps=args.eval_steps,
            save_strategy="steps",
            save_steps=args.save_steps,
            save_total_limit=3,
            seed=args.seed,
            fp16=False,
            bf16=True,
            optim="adamw_8bit",
            report_to="none",
            dataloader_pin_memory=True,
        )

        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=train_ds,
            eval_dataset=valid_ds,
            args=training_args,
            dataset_text_field="text",
            max_seq_length=args.max_seq_length,
            packing=False,
            callbacks=[make_ndjson_callback(total_steps)],
        )

        # ---- 9) 发送 train_start ----
        config_summary = {
            "model": args.model,
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "batch_size": args.batch_size,
            "grad_accumulation": args.grad_accumulation,
            "learning_rate": args.lr,
            "total_steps": total_steps,
            "train_samples": len(train_ds),
            "valid_samples": len(valid_ds),
        }
        emit("train_start", total_iters=total_steps, config=config_summary)

        # ---- 10) 开始训练 ----
        resume_from = None
        if args.resume:
            # 找最新 checkpoint
            ckpts = sorted(CHECKPOINT_DIR.glob("checkpoint-*"), key=os.path.getmtime)
            if ckpts:
                resume_from = str(ckpts[-1])
                emit_log(f"从 checkpoint 继续: {resume_from}")

        start_time = time.time()
        trainer.train(resume_from_checkpoint=resume_from)
        elapsed = time.time() - start_time

        # ---- 11) 保存最终 adapter ----
        final_adapter_dir = OUTPUT_DIR / "adapter"
        model.save_pretrained(str(final_adapter_dir))
        tokenizer.save_pretrained(str(final_adapter_dir))
        emit_log(f"Adapter 已保存到: {final_adapter_dir}")

        # ---- 12) 完成事件 ----
        emit("train_complete", success=True)
        emit("stage_complete", stage="train", data={"elapsed": round(elapsed, 1)})
        emit("pipeline_complete")

    except KeyboardInterrupt:
        emit_log("训练被用户中断")
        emit("train_complete", success=False, error="用户中断")
        emit("pipeline_error", stage="train", error="用户中断")

    except Exception as e:
        emit_log(f"训练异常: {e}")
        emit("train_complete", success=False, error=str(e))
        emit("pipeline_error", stage="train", error=str(e))


if __name__ == "__main__":
    main()

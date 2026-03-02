"""
AI 策略代码生成器 — 支持 OpenAI API 和本地模型 (MLX/Ollama)
"""
import os
import re
import json
import time
from typing import List, Dict, Tuple, Optional, Generator

from loguru import logger
from openai import OpenAI
from httpx import Timeout as HttpxTimeout

from alpha_lab.prompts.system import SYSTEM_PROMPT
from alpha_lab.prompts.generate import build_first_generate_prompt
from alpha_lab.prompts.iterate import build_iteration_prompt


def _get_provider_config() -> Dict:
    """读取 Alpha Lab 模型配置"""
    provider = os.getenv("ALPHA_LAB_PROVIDER", "openai").lower()

    if provider == "local":
        return {
            "provider": "local",
            "base_url": os.getenv("ALPHA_LAB_LOCAL_BASE_URL", "http://localhost:11434/v1"),
            "model": os.getenv("ALPHA_LAB_LOCAL_MODEL", "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit"),
            "api_key": "local",  # 本地模型不需要真实 key
        }
    else:
        return {
            "provider": "openai",
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            "explore_model": os.getenv("ALPHA_LAB_EXPLORE_MODEL", "gpt-4o-mini"),
            "refine_model": os.getenv("ALPHA_LAB_REFINE_MODEL", "gpt-4o"),
            "api_key": os.getenv("OPENAI_API_KEY", ""),
        }


class CodeGenerator:
    """AI 策略代码生成器"""

    def __init__(self):
        self.config = _get_provider_config()
        self.client: Optional[OpenAI] = None
        self._init_client()

    def _init_client(self):
        if self.config["provider"] == "local":
            self.client = OpenAI(
                api_key=self.config["api_key"],
                base_url=self.config["base_url"],
                timeout=HttpxTimeout(connect=15.0, read=300.0, write=30.0, pool=30.0),
                max_retries=1,
            )
            logger.info(f"Alpha Lab 使用本地模型: {self.config['model']} @ {self.config['base_url']}")
        else:
            api_key = self.config["api_key"]
            if api_key:
                self.client = OpenAI(
                    api_key=api_key,
                    base_url=self.config["base_url"],
                    timeout=HttpxTimeout(connect=15.0, read=120.0, write=30.0, pool=30.0),
                    max_retries=2,
                )
                logger.info(f"Alpha Lab 使用 OpenAI API")

    def get_model_for_phase(self, phase: str) -> str:
        """根据阶段返回模型名"""
        if self.config["provider"] == "local":
            return self.config["model"]  # 本地模型不分阶段
        return self.config.get("explore_model", "gpt-4o-mini") if phase == "explore" else self.config.get("refine_model", "gpt-4o")

    def is_local(self) -> bool:
        return self.config["provider"] == "local"

    def generate(
        self,
        messages: List[Dict],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
    ) -> Tuple[str, str, int]:
        """
        调用 AI 生成策略代码

        Returns:
            (strategy_code, full_response, tokens_used)
        """
        if not self.client:
            raise RuntimeError("AI 客户端未初始化，请检查 .env 配置")

        logger.info(f"调用 {model} 生成策略代码...")

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=4000,
        )

        content = response.choices[0].message.content or ""
        tokens = response.usage.total_tokens if response.usage else 0

        # 提取代码块
        code = self._extract_code(content)

        logger.info(f"代码生成完成，tokens={tokens}")
        return code, content, tokens

    def generate_stream(
        self,
        messages: List[Dict],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
    ) -> Generator[Dict, None, None]:
        """
        流式生成策略代码

        Yields:
            {"event": "chunk", "content": "..."} — 内容片段
            {"event": "code_ready", "code": "...", "full_response": "...", "tokens": int}
            {"event": "error", "message": "..."}
        """
        if not self.client:
            yield {"event": "error", "message": "AI 客户端未初始化"}
            return

        try:
            stream_kwargs = dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=4000,
                stream=True,
            )

            try:
                stream = self.client.chat.completions.create(
                    **stream_kwargs,
                    stream_options={"include_usage": True},
                )
            except Exception:
                stream = self.client.chat.completions.create(**stream_kwargs)

            full_content = ""
            token_count = 0

            for chunk in stream:
                if chunk.usage:
                    token_count = chunk.usage.total_tokens
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        full_content += delta.content
                        yield {"event": "chunk", "content": delta.content}

            # 提取代码
            try:
                code = self._extract_code(full_content)
                yield {
                    "event": "code_ready",
                    "code": code,
                    "full_response": full_content,
                    "tokens": token_count,
                }
            except ValueError as e:
                yield {"event": "error", "message": str(e)}

        except Exception as e:
            logger.error(f"AI 代码生成失败: {e}")
            yield {"event": "error", "message": f"AI 调用失败: {e}"}

    @staticmethod
    def _extract_code(content: str) -> str:
        """从 AI 回复中提取 Python 代码块"""
        pattern = r"```python\s*\n(.*?)```"
        matches = re.findall(pattern, content, re.DOTALL)
        if not matches:
            # 尝试无语言标记的代码块
            pattern2 = r"```\s*\n(.*?)```"
            matches = re.findall(pattern2, content, re.DOTALL)
        if not matches:
            raise ValueError("AI 回复中未找到 Python 代码块，请重试")
        # 取最长的代码块（通常是策略主体）
        code = max(matches, key=len).strip()
        return code

    @staticmethod
    def build_initial_messages(
        symbols: List[str],
        optimization_goal: str,
        data_summary: Dict,
        constraints: Optional[Dict] = None,
    ) -> List[Dict]:
        """构建首次生成的 messages"""
        user_prompt = build_first_generate_prompt(
            symbols=symbols,
            optimization_goal=optimization_goal,
            data_summary=data_summary,
            constraints=constraints,
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    @staticmethod
    def build_iteration_messages(
        messages: List[Dict],
        iteration: int,
        prev_train_metrics: Dict,
        prev_val_metrics: Dict,
        prev_overfit_score: float,
        prev_overfit_warnings: List[str],
        prev_composite_score: float,
        best_iteration: Optional[int],
        best_val_sharpe: Optional[float],
        best_composite_score: Optional[float],
        phase: str,
    ) -> List[Dict]:
        """构建迭代优化的 messages（在原有对话基础上追加）"""
        user_prompt = build_iteration_prompt(
            iteration=iteration,
            prev_train_metrics=prev_train_metrics,
            prev_val_metrics=prev_val_metrics,
            prev_overfit_score=prev_overfit_score,
            prev_overfit_warnings=prev_overfit_warnings,
            prev_composite_score=prev_composite_score,
            best_iteration=best_iteration,
            best_val_sharpe=best_val_sharpe,
            best_composite_score=best_composite_score,
            phase=phase,
        )
        return messages + [{"role": "user", "content": user_prompt}]

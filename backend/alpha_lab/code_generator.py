"""
AI 策略代码生成器 — 调用 OpenAI 生成策略代码
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


class CodeGenerator:
    """AI 策略代码生成器"""

    def __init__(self):
        self.client: Optional[OpenAI] = None
        self._init_client()

    def _init_client(self):
        api_key = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if api_key:
            self.client = OpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=HttpxTimeout(connect=15.0, read=120.0, write=30.0, pool=30.0),
                max_retries=2,
            )

    def generate(
        self,
        messages: List[Dict],
        model: str = "gpt-4o-mini",
        temperature: float = 0.7,
    ) -> Tuple[str, str, int]:
        """
        调用 AI 生成策略代码

        Args:
            messages: OpenAI messages 列表
            model: 模型名
            temperature: 创造性温度

        Returns:
            (strategy_code, full_response, tokens_used)
        """
        if not self.client:
            raise RuntimeError("OPENAI_API_KEY 未配置，请在 .env 中设置")

        logger.info(f"调用 {model} 生成策略代码...")

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=3000,
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
            yield {"event": "error", "message": "OPENAI_API_KEY 未配置"}
            return

        try:
            stream_kwargs = dict(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=3000,
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

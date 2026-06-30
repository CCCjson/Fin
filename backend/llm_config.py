"""
LLM 模型配置 — 统一从环境变量读取，按用途分两档。

策略（Jason 定）：真正落到投资建议的用最强模型，其余全部用便宜的。
- 最强档 (OPENAI_BEST_MODEL)：AI 投资顾问、AI 报告等直接产出投资建议的模块
- 便宜档 (OPENAI_CHEAP_MODEL)：新闻分析/情感、每日复盘评分、Alpha Lab 代码生成等

用函数（每次读 env），避免 dotenv 加载顺序导致模块级常量取不到值。
"""
import os


def get_best_model() -> str:
    """最强档模型 — 用于真正落到投资建议的场景"""
    return os.getenv("OPENAI_BEST_MODEL", "gpt-5.5")


def get_cheap_model() -> str:
    """便宜档模型 — 用于信息加工、打分等非建议类场景"""
    return os.getenv("OPENAI_CHEAP_MODEL", "gpt-5.4-mini")


def _is_gpt5_family(model: str) -> bool:
    """是否为 GPT-5 / o 系列（这类模型有新的参数约束）"""
    m = str(model or "")
    return m.startswith(("gpt-5", "o1", "o3", "o4"))


def normalize_chat_params(params: dict) -> dict:
    """
    规范化 chat.completions.create 参数，兼容 GPT-5 / o 系列的新约束：
    - max_tokens → max_completion_tokens（GPT-5 不再支持 max_tokens）
    - temperature 仅支持默认值 1，移除任何自定义值

    仅对 GPT-5/o 系列生效；Claude 中转站、本地模型等保持原样。
    """
    p = dict(params)
    if _is_gpt5_family(p.get("model", "")):
        if "max_tokens" in p:
            p["max_completion_tokens"] = p.pop("max_tokens")
        if p.get("temperature", 1) != 1:
            p.pop("temperature", None)
    return p

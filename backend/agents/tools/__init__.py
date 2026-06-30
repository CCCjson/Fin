"""
工具集合 —— import 本包即触发所有子模块的工具注册（@tool 装饰器副作用）。

新增工具时：建/改对应域的子模块，并确保在此处 import。
"""
from agents.tools import data_tools       # noqa: F401
from agents.tools import analysis_tools   # noqa: F401
from agents.tools import portfolio_tools  # noqa: F401
from agents.tools import trading_tools    # noqa: F401

# 外置金融大脑：search_knowledge 等知识库工具（注册靠 import 副作用）
from knowledge_engine import tools as _knowledge_tools  # noqa: F401

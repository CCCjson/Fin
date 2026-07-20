"""
工具集合 —— import 本包即触发所有子模块的工具注册（@tool 装饰器副作用）。

新增工具时：建/改对应域的子模块，并确保在此处 import。
"""
from agents.tools import data_tools       # noqa: F401
from agents.tools import analysis_tools   # noqa: F401
from agents.tools import portfolio_tools  # noqa: F401
from agents.tools import trading_tools    # noqa: F401
from agents.tools import news_tools       # noqa: F401
from agents.tools import backtest_tools   # noqa: F401  回测（C++ 正版）
from agents.tools import review_tools     # noqa: F401  每日复盘 + AI 评分
from agents.tools import pool_tools       # noqa: F401  指数/行业成分股
from agents.tools import monitor_tools    # noqa: F401  系统脉搏 + 业务事件
from agents.tools import settings_tools   # noqa: F401  系统配置读写
from agents.tools import nav_tools        # noqa: F401  agent 驱动前端导航
from agents.tools import recommend_tools   # noqa: F401  按今日行情选股推荐
from agents.tools import signal_tools     # noqa: F401  信号查询 + 胜率统计
from agents.tools import screener_tools   # noqa: F401  多因子选股筛选
from agents.tools import alert_tools      # noqa: F401  价格预警 CRUD
from agents.tools import decision_tools   # noqa: F401  决策留痕查询
from agents.tools import watchlist_tools  # noqa: F401  自选股管理
from agents.tools import market_tools     # noqa: F401  大盘环境脉搏
from agents.tools import intraday_tools   # noqa: F401  个股盘中体检
from agents.tools import limit_up_tools   # noqa: F401  涨停池复盘 + 次日候选池预测
from agents.tools import knowledge_tools  # noqa: F401  外置金融大脑：知识库 + 联网层
from agents.tools import crypto_tools     # noqa: F401  加密货币（币安现货）行情/排雷/衍生品/下单

# 工具分组元工具 load_toolgroup 现在从 agents/__init__.py 最后统一 import——
# tool_groups.py 的分组现在按 ToolDef.group 从 REGISTRY 动态生成（Phase 9），
# 必须等 subagents 也注册完才能算，不能在这里提前触发。

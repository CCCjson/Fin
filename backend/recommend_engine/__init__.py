"""选股推荐引擎 —— 候选双源合并 + 三重闸门 BUY 判定 + 持仓 SELL/HOLD 分流。

从 agents/tools/recommend_tools.py 下沉（引擎+工具两层架构）：
- session: 交易时段判断（A股东八区），被多个工具文件复用
- candidates: 候选来源（信号表 / 基本面选股器 / 盘中分钟线）+ 名称/主板/价格小工具
- scoring: cockpit 深度打分 + 并发
- engine: recommend() 主流水线
"""
from recommend_engine.engine import recommend
from recommend_engine.session import _now_sh, _session_phase

__all__ = ["recommend", "_now_sh", "_session_phase"]

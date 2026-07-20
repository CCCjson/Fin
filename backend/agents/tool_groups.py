"""
工具分组 —— 核心常驻 + 按需加载，压掉「46 个 schema 每轮全发」的固定 token 地板。

分组成员现在直接来自每个工具注册时声明的 ToolDef.group 字段（单一数据源），
不再是这里手写维护的第二份"组名→工具名集合"清单——新增/删除工具只需要在
工具自己的 @tool(...) 里改 group=，不需要跑来这个文件同步，也就不存在
"忘了同步导致漂移"这类 bug 了（Phase 9，归并进 registry.py::ToolDef.group）。
这里只保留"组名→一句话描述"这种低频变化的元数据（供 load_toolgroup 的工具
描述 + 系统提示能力地图使用）。

重要：本模块必须在 agents/tools 与 agents/subagents 都注册完之后才能 import
（见 agents/__init__.py 的 import 顺序），否则 _registry_groups() 的 lru_cache
会把一份不完整的分组结果冻结下来。

三层曝光策略：
  1. CORE_TOOLS 常驻（高频 + 下单确认流依赖的必须在内，group="core"）
  2. 按页面路由预载对应组（PAGE_GROUPS）
  3. 模型自助扩容：调 load_toolgroup 元工具加载缺的组（orchestrator 特判，session 内粘滞）

回滚开关：环境变量 AGENT_TOOL_GROUPS=off → 恢复全量暴露。
"""
import os
from functools import lru_cache
from typing import Optional

from agents.registry import tool

# 组名 → 一句话描述（新增一个全新的组名才需要改这里；给已有组添加工具不用）
_GROUP_DESCRIPTIONS: dict[str, str] = {
    "signals": "查询买卖信号与策略胜率统计 + 触发信号生成",
    "screener": "按条件筛股 + 指数/行业成分股圈范围",
    "watchlist_alerts": "自选股增删查 + 价格预警管理",
    "portfolio_risk": "持仓止损监护/组合风险体检/账实对账/交易流水/补录真实成交",
    "news": "个股舆情分数 + 盘前简报",
    "knowledge_web": ("本地知识库检索 + 联网搜索(web/SEC) + 读网页 + "
                      "逆向API爬取网站数据(scrape 一步搞定,需登录站先 login_site 人工登) + alpha 想法"),
    "review": "每日复盘/AI 交易打分/历史决策留痕查询",
    "backtest_ml": "策略回测(C++ 引擎) + ML 涨跌预测",
    "system": "系统健康脉搏 + 配置读改",
    "deep_agents": ("深度任务子代理：个股深度研判/新闻深度解读/策略研发 + "
                    "五个投研报告章节(大盘板块/新闻舆情/持仓诊断/回顾策略/买入推荐)，可单调可组合"),
    "market_sentiment": "涨停池全览(家数/连板梯队/炸板率/赚钱效应) + 次日涨停候选池预测",
    "crypto": ("加密货币(币安现货)：市场大势(恐慌贪婪/BTC主导率/200日牛熊) + 单币排雷体检 + "
               "衍生品情绪(资金费率/OI/多空比) + 币安账户查询（下单工具 place_crypto_order 在 core 组）"),
}

# 前端路由前缀 → 预载组（match 用 startswith，前缀长的先试）
# 架构收敛后仅保留 8 个工作台页面，退役页面的能力靠模型自助 load_toolgroup
PAGE_GROUPS: dict[str, str] = {
    "/app/backtest": "backtest_ml",
    "/app/prediction": "backtest_ml",
    "/app/data-monitor": "system",
}

META_TOOL = "load_toolgroup"
_CORE_GROUP_NAME = "core"


def grouping_enabled() -> bool:
    return os.getenv("AGENT_TOOL_GROUPS", "on").lower() not in ("off", "0", "false")


@lru_cache(maxsize=1)
def _registry_groups() -> tuple[frozenset, "dict[str, frozenset]"]:
    """按 ToolDef.group 对 REGISTRY 全量工具分类，返回 (core 工具集合, {组名: 工具集合})。

    取代旧版 _validate()：漂移检测从"两份手写清单对比"变成"每个工具是否声明了
    group、group 名是否有对应描述"——因为现在只剩一份数据源（ToolDef.group），
    没有第二份清单可比对，这类检测自然式微。
    """
    from agents.registry import REGISTRY
    core: set[str] = set()
    groups: dict[str, set[str]] = {}
    ungrouped: list[str] = []
    for td in REGISTRY.all():
        if td.name == META_TOOL:
            continue
        if not td.group:
            ungrouped.append(td.name)
            continue
        if td.group == _CORE_GROUP_NAME:
            core.add(td.name)
        else:
            groups.setdefault(td.group, set()).add(td.name)
    if ungrouped:
        raise RuntimeError(
            f"tool_groups: 以下工具未声明 group，请在 @tool(...) 里补上：{sorted(ungrouped)}")
    unknown_groups = set(groups) - set(_GROUP_DESCRIPTIONS)
    if unknown_groups:
        raise RuntimeError(
            f"tool_groups: 出现未登记描述的新组名 {sorted(unknown_groups)}，"
            "请在 agents/tool_groups.py::_GROUP_DESCRIPTIONS 里补一句话描述。")
    return frozenset(core), {g: frozenset(s) for g, s in groups.items()}


def _core_tools() -> frozenset:
    return _registry_groups()[0]


def _tool_groups() -> "dict[str, frozenset]":
    return _registry_groups()[1]


def initial_allowed(page_path: Optional[str]) -> set[str]:
    """会话初始可见集：核心 + 元工具 + 当前页面对应组。"""
    allowed = set(_core_tools()) | {META_TOOL}
    if page_path:
        tool_groups = _tool_groups()
        for prefix, group in sorted(PAGE_GROUPS.items(), key=lambda kv: -len(kv[0])):
            if page_path.startswith(prefix):
                allowed |= tool_groups[group]
                break
    return allowed


def expand(allowed: set[str], groups: list[str]) -> tuple[set[str], list[str], list[str]]:
    """加载若干组，返回 (新 allowed, 新增工具名列表, 未知组名列表)。"""
    tool_groups = _tool_groups()
    added: list[str] = []
    unknown: list[str] = []
    new_allowed = set(allowed)
    for g in groups:
        if g not in tool_groups:
            unknown.append(g)
            continue
        names = tool_groups[g]
        added.extend(sorted(names - new_allowed))
        new_allowed |= names
    return new_allowed, added, unknown


def group_names() -> list[str]:
    """所有已知组名（供 orchestrator 报"未知组名"提示时列出可用组）。"""
    return sorted(_tool_groups().keys())


def capability_map() -> str:
    """给 system prompt 用的能力地图（每组一行）。"""
    lines = [f"- {g}：{_GROUP_DESCRIPTIONS[g]}" for g in _tool_groups()]
    return "\n".join(lines)


def _group_inventory() -> str:
    tool_groups = _tool_groups()
    lines = []
    for g in tool_groups:
        lines.append(f"{g}（{_GROUP_DESCRIPTIONS[g]}）: {', '.join(sorted(tool_groups[g]))}")
    return "；".join(lines)


@tool(
    name=META_TOOL,
    description=(
        "加载额外的工具组，让对应工具在本会话内可调用。当你需要的能力不在当前工具列表时先调它。"
        "可用组及各组包含的工具：" + _group_inventory()
    ),
    parameters={
        "type": "object",
        "properties": {
            "groups": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(_tool_groups().keys())},
                "description": "要加载的工具组名，可一次多个",
            },
        },
        "required": ["groups"],
    },
    category="meta",
)
def load_toolgroup(groups: list[str]) -> dict:
    # orchestrator 特判分流（需改 session.allowed_tools），这里只是兜底
    return {"summary": f"工具组 {groups} 已请求加载"}

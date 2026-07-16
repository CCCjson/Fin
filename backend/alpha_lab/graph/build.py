"""
alpha_lab 循环子图装配 —— 薄用 `StateGraph`（CODING_STANDARDS §6 四原语之一）。

图形状（对齐深任务统一形状 Plan→Execute→Evaluate→(loop/done)）：

    START → prepare → [data_ready?] → generate → ast_check → backtest → evaluate → advance
                          │ 否                                                        │
                          ▼                                          [继续/早停/满轮?] │
                       finalize ◄──────────────────────────────────────────────────┘
                          │
                          ▼
                         END

checkpointer 在**节点边界**落盘：backtest（最贵，90s）完成即持久化，其后任一节点崩溃
都能从下一个节点 resume 而不重跑回测——这是断点续跑的核心收益。
"""
from langgraph.graph import END, START, StateGraph

from alpha_lab.graph.nodes import (
    AlphaLabNodes,
    route_after_advance,
    route_after_prepare,
)
from alpha_lab.graph.state import AlphaLabGraphState


def build_graph(nodes: AlphaLabNodes, checkpointer=None):
    """装配并编译 alpha_lab 循环子图。

    Args:
        nodes: 持有各组件实例的节点集合
        checkpointer: sqlite checkpointer（None 时无持久化，用于纯逻辑测试）

    Returns:
        编译后的图（graph.stream(state, config, stream_mode="custom") 逐条产事件）
    """
    g = StateGraph(AlphaLabGraphState)

    g.add_node("prepare", nodes.prepare)
    g.add_node("generate", nodes.generate)
    g.add_node("ast_check", nodes.ast_check)
    g.add_node("backtest", nodes.backtest)
    g.add_node("evaluate", nodes.evaluate)
    g.add_node("advance", nodes.advance)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "prepare")
    g.add_conditional_edges("prepare", route_after_prepare,
                            {"generate": "generate", "finalize": "finalize"})
    # 单轮执行链（串行，无并行超步）
    g.add_edge("generate", "ast_check")
    g.add_edge("ast_check", "backtest")
    g.add_edge("backtest", "evaluate")
    g.add_edge("evaluate", "advance")
    # 循环回边 / 收尾
    g.add_conditional_edges("advance", route_after_advance,
                            {"generate": "generate", "finalize": "finalize"})
    g.add_edge("finalize", END)

    return g.compile(checkpointer=checkpointer)

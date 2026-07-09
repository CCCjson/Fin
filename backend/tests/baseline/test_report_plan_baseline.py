"""13.1 基线：report 的章节编排结构。

这是 13.2「report 子图 = Planner 动态编排 + DAG 并发」改造的直接对照物。
当下的行为是：固定 8 章、串行链式、第 8 章按批次链式去重、第 1 章最后写。
迁移后 planner 会动态决定章节数，届时本文件的 golden 会红——**那是预期的**，
届时要做的是人工 diff 确认「动态编排产出 ≥ 固定编排」，再更新 golden。

只锁**结构**（章节号 / 顺序 / 批次 / 温度 / 依赖图），不锁 prompt 文本 ——
prompt 措辞的正常演进不该让基线红。
"""
import pytest

from report_engine.generator import ReportGenerator
from report_engine.planner import CHAPTER_DEPS, ReportPlan, ReportPlanner

pytestmark = pytest.mark.baseline


# 当前（迁移前）的章节调用顺序快照：(call_index, chapters, temperature, needs_previous_outputs)
GOLDEN_CALL_SEQUENCE = [
    (1,  [2], 0.5, False),   # 市场总览与情绪研判
    (2,  [3], 0.5, False),   # 新闻深度分析
    (3,  [4], 0.5, False),   # 板块热点与北向资金
    (4,  [6], 0.4, False),   # 上期推荐回顾
    (5,  [5], 0.4, False),   # 持仓诊断
    (6,  [7], 0.4, False),   # 信号与策略表现
    (7,  [8], 0.3, False),   # 买入推荐 第1批
    (8,  [8], 0.3, False),   # 买入推荐 第2批
    (9,  [8], 0.3, False),   # 买入推荐 第3批
    (10, [1], 0.4, True),    # 周度纵览：需要前面所有章节的产出
]

GOLDEN_CHAPTER_DEPS = {
    2: [],
    3: [2],
    4: [2, 3],
    6: [2, 4],
    5: [2, 3, 4, 6],
    7: [2, 3, 4],
    8: [2, 3, 4, 5, 7],
}


def test_plan_call_sequence_snapshot(report_data_weekly):
    plan = ReportPlanner().plan(report_data_weekly, "weekly")
    actual = [
        (s.call_index, s.chapters, s.temperature, s.needs_previous_outputs)
        for s in plan.specs
    ]
    assert actual == GOLDEN_CALL_SEQUENCE


def test_chapter_dependency_dag_snapshot(report_data_weekly):
    plan = ReportPlanner().plan(report_data_weekly, "weekly")
    assert plan.deps == GOLDEN_CHAPTER_DEPS
    assert plan.deps is CHAPTER_DEPS, "deps 应直接引用真源对象，不要各存副本"


def test_plan_returns_report_plan(report_data_weekly):
    plan = ReportPlanner().plan(report_data_weekly, "weekly")
    assert isinstance(plan, ReportPlan)
    assert len(plan.specs) == 10


def test_generator_and_planner_share_one_chapter_deps_object():
    """两处各存一份依赖图，迟早会漂。锁死它们是同一个对象。"""
    assert ReportGenerator is not None  # 触发 import
    from report_engine import generator

    assert generator._CHAPTER_DEPS is CHAPTER_DEPS


def test_chapter_8_is_batched_and_chained(report_data_weekly):
    """第 8 章（买入推荐）分批链式生成：后一批要看到前一批推过的标的才能去重。

    这是章节并发化时最容易踩的坑——8 章内部的批次之间**不能**并发。
    """
    plan = ReportPlanner().plan(report_data_weekly, "weekly")
    ch8_calls = [s for s in plan.specs if s.chapters == [8]]
    assert len(ch8_calls) == 3
    indices = [s.call_index for s in ch8_calls]
    assert indices == sorted(indices), "第 8 章各批次必须保持递增顺序"


def test_chapter_1_is_last_and_needs_previous_outputs(report_data_weekly):
    """第 1 章是全局纵览，必须最后写、且能看到前面所有章节。"""
    plan = ReportPlanner().plan(report_data_weekly, "weekly")
    last = plan.specs[-1]
    assert last.chapters == [1]
    assert last.needs_previous_outputs is True
    assert 1 not in CHAPTER_DEPS, "第 1 章走 needs_previous_outputs 路径，不该出现在 DAG 里"


def test_no_chapter_depends_on_itself_and_dag_is_acyclic():
    """依赖图必须是 DAG —— 13.2 要按它做拓扑并发，有环就死循环。"""
    visited: dict[int, int] = {}  # 0=未访问 1=访问中 2=已完成

    def visit(node: int) -> None:
        state = visited.get(node, 0)
        assert state != 1, f"章节依赖图有环，卡在第 {node} 章"
        if state == 2:
            return
        visited[node] = 1
        for dep in CHAPTER_DEPS.get(node, []):
            assert dep != node, f"第 {node} 章依赖自己"
            visit(dep)
        visited[node] = 2

    for chapter in CHAPTER_DEPS:
        visit(chapter)


def test_dag_allows_expected_parallelism():
    """记录当前 DAG 的并发潜力：无依赖的章节可以同时跑。

    Ch2 是唯一的根。13.2 并发化后应至少能把 Ch3/Ch4 之后的 Ch6/Ch7 并起来。
    """
    roots = [ch for ch, deps in CHAPTER_DEPS.items() if not deps]
    assert roots == [2], "当前只有 Ch2 无依赖"

    # 拿到 Ch2/Ch3/Ch4 后，Ch6 与 Ch7 的依赖同时满足 → 可并发
    done = {2, 3, 4}
    ready = [ch for ch, deps in CHAPTER_DEPS.items() if ch not in done and set(deps) <= done]
    assert set(ready) == {6, 7}

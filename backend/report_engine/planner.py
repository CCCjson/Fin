"""
ReportPlanner —— 把 build_multi() 产出的调用清单 + 章节依赖图，显式建模成一个
ReportPlan 对象。

章节依赖图（哪章需要哪些前置章节的结论）是 report_type 的确定性函数，生成过程
不需要人工介入、也不依赖运行时才知道的分支——这是 Plan-and-Execute 的教科书
场景（已知结构的批量内容生成），而不是需要临场应变的 ReAct 场景。

CHAPTER_DEPS 从 generator.py 移过来，成为 Plan 的一部分（generator.py 反向
import 回去，行为不变，只是数据源移了个家）。

范围说明（诚实记录，不假装做了没做的事）：本次只做了"显式建模"这一步——把
隐式的 Plan(调用清单+依赖图) 变成一个显式对象，供未来的 ChapterExecutor 使用。
未实现章节间真正的并发调度：generator.py::generate_multi_stream 内部的章节
串行执行、Ch8 多批次链式去重（后一批依赖前一批已推荐标的）等逻辑保持原样未动。
并发化需要先验证前端是否假设章节 chunk 按到达顺序连续渲染（并行后 ch6/ch7 的
chunk 可能交错到达）——这一验证不在本次改动范围内，留作后续独立工作。
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from report_engine.prompt_builder import CallSpec

CHAPTER_DEPS: Dict[int, List[int]] = {
    2: [],              # Ch2 市场总览
    3: [2],             # Ch3 新闻 → 需 Ch2
    4: [2, 3],          # Ch4 板块 → 需 Ch2+Ch3(新闻)
    6: [2, 4],          # Ch6 上期回顾 → 需 Ch2+Ch4(板块)
    5: [2, 3, 4, 6],    # Ch5 持仓诊断 → 需 Ch2+Ch3+Ch4+Ch6
    7: [2, 3, 4],       # Ch7 策略 → 需 Ch2+Ch3+Ch4
    8: [2, 3, 4, 5, 7], # Ch8 买入推荐 → 需全部
    # Ch1 走 needs_previous_outputs=True 路径，不经过此映射
}


@dataclass
class ReportPlan:
    specs: "List[CallSpec]"
    deps: Dict[int, List[int]]


class ReportPlanner:
    def plan(self, data: dict, report_type: str = "weekly") -> ReportPlan:
        from report_engine.prompt_builder import ReportPromptBuilder
        specs = ReportPromptBuilder().build_multi(data, report_type)
        return ReportPlan(specs=specs, deps=CHAPTER_DEPS)

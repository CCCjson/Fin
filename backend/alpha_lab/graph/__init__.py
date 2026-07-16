"""
alpha_lab LangGraph 图路径（阶段A 试点）。

薄用 LangGraph 四原语（StateGraph + sqlite checkpointer + interrupt + subgraph，
见 docs/CODING_STANDARDS.md §6）把 alpha_lab evaluator-optimizer 循环迁成子图，
补上「进程重启/断连后从第 i 轮恢复」的断点续跑能力。

**整个 graph/ 子包可回滚删除**：由 `GRAPH_ALPHA_LAB=off`（默认）在 subagent 层
回落旧 `AlphaLabEngine`；删除本目录后旧引擎仍完整（共享数据准备在 alpha_lab.data_prep）。
"""

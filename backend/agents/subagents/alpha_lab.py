"""策略研发 subagent —— 复用 AlphaLabEngine.start_session（生成→沙箱→回测→评分 迭代闭环）。"""
import json
from datetime import datetime, timedelta
from typing import Generator

from loguru import logger

from agents.events import EV, emit
from agents.subagents.base import SubagentRunner
from agents.widgets import metric_cards_widget


class AlphaLabSubagent(SubagentRunner):
    name = "run_alpha_lab"

    def run(self, args: dict) -> Generator[str, None, None]:
        symbols = args.get("symbols") or args.get("target_symbols")
        if isinstance(symbols, str):
            symbols = [symbols]
        if not symbols:
            yield emit("subagent_done", result={"summary": "缺少 symbols，无法研发策略", "widgets": []})
            return
        goal = args.get("goal") or args.get("optimization_goal") or "sharpe"
        end = datetime.now()
        data_end = args.get("data_end") or end.strftime("%Y-%m-%d")
        data_start = args.get("data_start") or (end - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
        max_iter = int(args.get("max_iterations", 8))

        from alpha_lab.engine import AlphaLabEngine

        best = None
        complete = None
        try:
            gen = AlphaLabEngine().start_session(
                target_symbols=symbols, optimization_goal=goal,
                data_start=data_start, data_end=data_end, max_iterations=max_iter,
            )
            for line in gen:
                line = (line or "").strip()
                if not line:
                    continue
                ev = json.loads(line)
                et = ev.get("event")
                if et == "iteration_start":
                    yield emit(EV.AGENT_PROGRESS, agent=self.name,
                               message=f"第 {ev.get('iteration')} 轮（{ev.get('phase')}）：生成策略中…")
                elif et == "backtest_running":
                    yield emit(EV.AGENT_PROGRESS, agent=self.name,
                               message=f"第 {ev.get('iteration')} 轮：回测中…")
                elif et == "iteration_complete":
                    flag = "　⭐ 新最佳" if ev.get("is_best") else ""
                    yield emit(EV.AGENT_PROGRESS, agent=self.name,
                               message=(f"第 {ev.get('iteration')} 轮完成：训练Sharpe {ev.get('train_sharpe')} / "
                                        f"验证Sharpe {ev.get('val_sharpe')} / 综合分 {ev.get('composite_score')}{flag}"))
                    if ev.get("is_best"):
                        best = ev
                elif et == "session_complete":
                    complete = ev
                elif et in ("error", "iteration_error", "backtest_failed", "code_error"):
                    yield emit(EV.AGENT_PROGRESS, agent=self.name,
                               message=f"⚠️ {ev.get('error') or ev.get('message')}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"alpha_lab subagent 失败: {e}")
            yield emit("subagent_done", result={"summary": f"策略研发失败：{e}", "widgets": []})
            return

        # alpha_lab 自带美元成本，直接计入用量监视器
        try:
            tc = float((complete or {}).get("total_cost_usd") or 0)
            if tc:
                from agents.usage import USAGE
                USAGE.record_cost("alpha_lab", tc)
        except (TypeError, ValueError):
            pass

        if complete is None:
            yield emit("subagent_done", result={"summary": "策略研发未正常完成（可能数据或模型未就绪）", "widgets": []})
            return

        widgets = []
        if best:
            widgets.append(metric_cards_widget([
                {"label": "训练 Sharpe", "value": str(best.get("train_sharpe")), "type": "quality"},
                {"label": "验证 Sharpe", "value": str(best.get("val_sharpe")), "type": "quality"},
                {"label": "综合评分", "value": str(best.get("composite_score")), "type": "quality"},
                {"label": "过拟合分", "value": str(best.get("overfit_score")), "type": "risk"},
            ], title=f"最佳策略（第 {complete.get('best_iteration')} 轮）"))

        summary = (f"策略研发完成：最佳第 {complete.get('best_iteration')} 轮，"
                   f"验证 Sharpe {complete.get('best_sharpe')}，共 {complete.get('total_iterations')} 轮，"
                   f"花费 ${complete.get('total_cost_usd')}。session_id={complete.get('session_id')}")
        yield emit("subagent_done", result={"summary": summary, "widgets": widgets})

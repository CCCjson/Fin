"""策略研发 subagent —— 复用 AlphaLabEngine.start_session（生成→沙箱→回测→评分 迭代闭环）。"""
import json
from datetime import datetime, timedelta
from typing import Generator

from loguru import logger

from agents.events import EV, emit
from agents.subagents.base import SubagentRunner, emit_subagent_done
from agents.tool_envelope import ErrorCode, ToolEnvelope
from agents.widgets import metric_cards_widget


class AlphaLabSubagent(SubagentRunner):
    name = "run_alpha_lab"

    def run(self, args: dict, cancel_event=None) -> Generator[str, None, None]:
        symbols = args.get("symbols") or args.get("target_symbols")
        if isinstance(symbols, str):
            symbols = [symbols]
        if not symbols:
            yield emit_subagent_done(ToolEnvelope(
                ok=False, error_code=ErrorCode.VALIDATION_ERROR,
                message="缺少 symbols，无法研发策略"))
            return
        goal = args.get("goal") or args.get("optimization_goal") or "sharpe"
        end = datetime.now()
        data_end = args.get("data_end") or end.strftime("%Y-%m-%d")
        data_start = args.get("data_start") or (end - timedelta(days=3 * 365)).strftime("%Y-%m-%d")
        # 钳到 [1,20]：max_iterations 来自模型可控的 tool args，无上限的话
        # 模型传个大数会让引擎连转几十轮（每轮 LLM 生成 + 最长 90s 沙箱回测），
        # 长时间烧钱。20 轮足够覆盖 explore(5)+refine，超出无实际收益。
        try:
            max_iter = int(args.get("max_iterations", 8))
        except (TypeError, ValueError):
            max_iter = 8
        max_iter = max(1, min(max_iter, 20))

        from alpha_lab.engine import AlphaLabEngine

        best = None
        complete = None
        cancelled = False
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
                # 客户端断连（父 session 置位 cancel_event）：不再空等剩余迭代
                # （每轮 LLM 生成 + 最长 90s 沙箱回测），提前跳出，下面走 done 收尾。
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    yield emit(EV.AGENT_PROGRESS, agent=self.name,
                               message="客户端已断开，提前终止策略研发…")
                    break
        except Exception as e:  # noqa: BLE001
            logger.error(f"alpha_lab subagent 失败: {e}")
            yield emit_subagent_done(ToolEnvelope(
                ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                error_detail={"exception_type": type(e).__name__, "message": str(e)},
                message=f"策略研发失败：{e}"))
            return

        # alpha_lab 自带美元成本，直接计入用量监视器
        try:
            tc = float((complete or {}).get("total_cost_usd") or 0)
            if tc:
                from agents.usage import USAGE
                USAGE.record_cost("alpha_lab", tc)
        except (TypeError, ValueError):
            pass

        # 因客户端断开提前终止：这不是失败，是提前结束——优先把已完成轮次里的
        # 最佳结果带出去；一个 best 都没有才走 negative。都要走 done 收尾（不能静默退出）。
        if cancelled:
            if best:
                widget = metric_cards_widget([
                    {"label": "训练 Sharpe", "value": str(best.get("train_sharpe")), "type": "quality"},
                    {"label": "验证 Sharpe", "value": str(best.get("val_sharpe")), "type": "quality"},
                    {"label": "综合评分", "value": str(best.get("composite_score")), "type": "quality"},
                    {"label": "过拟合分", "value": str(best.get("overfit_score")), "type": "risk"},
                ], title=f"已完成轮次最佳策略（第 {best.get('iteration')} 轮）")
                yield emit_subagent_done(ToolEnvelope(
                    message=(f"因客户端断开提前终止，以下是已完成轮次里的最佳结果："
                             f"第 {best.get('iteration')} 轮，验证 Sharpe {best.get('val_sharpe')}，"
                             f"综合分 {best.get('composite_score')}。"),
                    widget=widget))
            else:
                yield emit_subagent_done(ToolEnvelope(
                    business_result="negative",
                    message="策略研发因客户端断开提前终止，尚未产生可用结果。"))
            return

        if complete is None:
            # 引擎循环结束但没能走到 session_complete——过程本身出了岔子（数据/模型未就绪
            # 一类），不是"研发完成但没找到好策略"这种诚实业务结论，归类为内部错误更准确。
            yield emit_subagent_done(ToolEnvelope(
                ok=False, error_code=ErrorCode.INTERNAL_ERROR,
                error_detail={"message": "session_complete 事件缺失"},
                message="策略研发未正常完成（可能数据或模型未就绪）"))
            return

        widget = None
        if best:
            widget = metric_cards_widget([
                {"label": "训练 Sharpe", "value": str(best.get("train_sharpe")), "type": "quality"},
                {"label": "验证 Sharpe", "value": str(best.get("val_sharpe")), "type": "quality"},
                {"label": "综合评分", "value": str(best.get("composite_score")), "type": "quality"},
                {"label": "过拟合分", "value": str(best.get("overfit_score")), "type": "risk"},
            ], title=f"最佳策略（第 {complete.get('best_iteration')} 轮）")

        summary = (f"策略研发完成：最佳第 {complete.get('best_iteration')} 轮，"
                   f"验证 Sharpe {complete.get('best_sharpe')}，共 {complete.get('total_iterations')} 轮，"
                   f"花费 ${complete.get('total_cost_usd')}。session_id={complete.get('session_id')}")
        yield emit_subagent_done(ToolEnvelope(message=summary, widget=widget))

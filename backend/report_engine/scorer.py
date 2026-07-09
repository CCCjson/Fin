"""
综合评分器 — 对买入信号进行多维度综合评分，筛选出真正值得推荐的标的

评分维度（满分100）：
1. 多策略共振  33%  — 同一只股票被多少个策略同时看好
2. 风险收益比  20%  — 盈亏比越高越好，保护投资者
3. 信号质量    12%  — 各策略 strength 的加权平均
4. 量能确认    10%  — 是否有放量配合
5. 信号时效    10%  — 距今越近越有操作价值
6. 策略回测     8%  — 触发信号的策略历史回测表现（Sharpe/胜率/样本量）
7. 标的质量     7%  — 庄股风险等级（low→100, medium→50, high→0）
"""
import json
import re
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple
from loguru import logger


# 各维度权重
W_RESONANCE = 0.33
W_RISK_REWARD = 0.20
W_QUALITY = 0.12
W_VOLUME = 0.10
W_TIMELINESS = 0.10
W_STRATEGY_BACKTEST = 0.08
W_STOCK_QUALITY = 0.07

# 各策略在信号质量维度的权重
STRATEGY_WEIGHTS = {
    "MACD": 0.30,
    "MA_CROSS": 0.25,
    "KDJ": 0.25,
    "RSI": 0.20,
}


class SignalScorer:
    """对买入/卖出信号进行综合评分"""

    def rank_buy_signals(
        self,
        signals: list,
        period_end: date,
        top_n: int = 10,
        min_score: float = 65.0,
        backtest_stats: Optional[Dict] = None,
        stock_analysis: Optional[Dict] = None,
    ) -> List[Dict]:
        """
        对 BUY 信号按综合评分排序，只返回达到质量门槛的标的

        Args:
            signals: Signal ORM 对象列表（已筛选 signal_type == "BUY"）
            period_end: 报告截止日期（用于时效性计算）
            top_n: 最多返回 N 只（软上限，质量不够就少于 N 只）
            min_score: 最低综合评分门槛，低于此分数的标的直接过滤（默认 65 分）
            backtest_stats: 各策略回测统计 {"MACD": {"sharpe_ratio": 1.2, "win_rate": 55, "total_trades": 120}, ...}
            stock_analysis: 各股票庄股风险分析 {"600519.SH": {"risk_level": "low", ...}, ...}

        Returns:
            按综合评分降序排列的列表，每项包含：
            {
                "symbol": "600519.SH",
                "composite_score": 78.5,
                "score_breakdown": {
                    "resonance": {"score": 80, "detail": "3个策略共振: MA_CROSS, MACD, KDJ"},
                    "risk_reward": {"score": 60, "detail": "盈亏比 1.8"},
                    "quality": {"score": 72, "detail": "加权强度 0.72"},
                    "volume": {"score": 75, "detail": "量比 1.8"},
                    "timeliness": {"score": 100, "detail": "0天前"},
                    "strategy_backtest": {"score": 85, "detail": "MACD Sharpe=1.2, 胜率55%"},
                    "stock_quality": {"score": 100, "detail": "标的质量良好"},
                },
                "signals": [Signal, ...],  # 该股票的所有 BUY 信号
                "best_signal": Signal,     # 最佳信号（用于展示）
            }
        """
        if not signals:
            return []

        backtest_stats = backtest_stats or {}
        stock_analysis = stock_analysis or {}

        # 按 (symbol, date) 分组
        grouped = self._group_signals(signals)

        scored_stocks = []
        for (symbol, sig_date), sig_list in grouped.items():
            scores = self._score_signal_group(sig_list, sig_date, period_end)
            # Step 4: 新增策略回测质量维度
            bt_score = self._score_strategy_backtest(sig_list, backtest_stats)
            scores["strategy_backtest"] = bt_score

            # 标的质量维度
            sq_score = self._score_stock_quality(symbol, stock_analysis)
            scores["stock_quality"] = sq_score

            composite = (
                scores["resonance"]["score"] * W_RESONANCE
                + scores["risk_reward"]["score"] * W_RISK_REWARD
                + scores["quality"]["score"] * W_QUALITY
                + scores["volume"]["score"] * W_VOLUME
                + scores["timeliness"]["score"] * W_TIMELINESS
                + bt_score["score"] * W_STRATEGY_BACKTEST
                + sq_score["score"] * W_STOCK_QUALITY
            )

            # 选最佳信号：共振策略中 strength 最高的那条
            best_signal = max(sig_list, key=lambda s: s.strength)

            scored_stocks.append({
                "symbol": symbol,
                "date": sig_date,
                "composite_score": round(composite, 1),
                "score_breakdown": scores,
                "signals": sig_list,
                "best_signal": best_signal,
            })

        # 按综合评分降序
        scored_stocks.sort(key=lambda x: x["composite_score"], reverse=True)

        # 去重：同一只股票只保留最高分的那天
        seen_symbols = set()
        unique_stocks = []
        for item in scored_stocks:
            if item["symbol"] not in seen_symbols:
                seen_symbols.add(item["symbol"])
                unique_stocks.append(item)

        # 质量门槛过滤：低于 min_score 的直接丢弃，不凑数
        qualified = [s for s in unique_stocks if s["composite_score"] >= min_score]

        if unique_stocks:
            logger.info(
                f"综合评分完成: {len(unique_stocks)} 只评分, "
                f"过门槛({min_score}分): {len(qualified)} 只, "
                f"Top 1 = {unique_stocks[0]['symbol']} ({unique_stocks[0]['composite_score']}分)"
            )
        else:
            logger.info("无买入信号")

        return qualified[:top_n]

    def rank_sell_signals(
        self,
        signals: list,
        period_end: date,
        top_n: int = 3,
    ) -> List[Dict]:
        """
        对 SELL 信号简单排序（共振 + strength）

        卖出信号不需要那么复杂的评分，主要看共振度和信号强度。
        """
        if not signals:
            return []

        grouped = self._group_signals(signals)

        scored = []
        for (symbol, sig_date), sig_list in grouped.items():
            num_strategies = len(set(s.strategy for s in sig_list))
            avg_strength = sum(s.strength for s in sig_list) / len(sig_list)
            # 简单评分：共振占60% + 强度占40%
            resonance_score = {1: 25, 2: 50, 3: 80, 4: 100}.get(num_strategies, 100)
            score = resonance_score * 0.6 + avg_strength * 100 * 0.4

            best_signal = max(sig_list, key=lambda s: s.strength)
            scored.append({
                "symbol": symbol,
                "date": sig_date,
                "composite_score": round(score, 1),
                "score_breakdown": {
                    "resonance": {
                        "score": resonance_score,
                        "detail": f"{num_strategies}个策略: {', '.join(set(s.strategy for s in sig_list))}",
                    },
                },
                "signals": sig_list,
                "best_signal": best_signal,
            })

        scored.sort(key=lambda x: x["composite_score"], reverse=True)

        # 去重
        seen = set()
        unique = []
        for item in scored:
            if item["symbol"] not in seen:
                seen.add(item["symbol"])
                unique.append(item)

        return unique[:top_n]

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _group_signals(self, signals: list) -> Dict[Tuple[str, date], list]:
        """按 (symbol, date) 分组"""
        grouped: Dict[Tuple[str, date], list] = defaultdict(list)
        for s in signals:
            grouped[(s.symbol, s.date)].append(s)
        return grouped

    def _score_signal_group(
        self,
        signals: list,
        sig_date: date,
        period_end: date,
    ) -> Dict[str, Dict]:
        """对同一 (symbol, date) 的信号组计算各维度分数"""
        return {
            "resonance": self._score_resonance(signals),
            "risk_reward": self._score_risk_reward(signals),
            "quality": self._score_quality(signals),
            "volume": self._score_volume(signals),
            "timeliness": self._score_timeliness(sig_date, period_end),
        }

    def _score_resonance(self, signals: list) -> Dict:
        """多策略共振评分"""
        strategies = list(set(s.strategy for s in signals))
        n = len(strategies)
        score_map = {1: 25, 2: 50, 3: 80, 4: 100}
        score = score_map.get(n, 100)
        detail = f"{n}个策略共振: {', '.join(strategies)}" if n > 1 else f"仅{strategies[0]}单策略"
        return {"score": score, "detail": detail}

    def _score_risk_reward(self, signals: list) -> Dict:
        """风险收益比评分"""
        ratios = []
        for s in signals:
            if s.price and s.stop_loss and s.take_profit:
                risk = s.price - s.stop_loss
                reward = s.take_profit - s.price
                if risk > 0:
                    ratios.append(reward / risk)

        if not ratios:
            return {"score": 40, "detail": "无止损止盈数据"}

        # 取最佳盈亏比（多策略中选最有利的）
        best_ratio = max(ratios)
        avg_ratio = sum(ratios) / len(ratios)

        if best_ratio >= 3.0:
            score = 100
        elif best_ratio >= 2.0:
            score = 80
        elif best_ratio >= 1.5:
            score = 60
        elif best_ratio >= 1.0:
            score = 30
        else:
            score = 10

        return {"score": score, "detail": f"盈亏比 {avg_ratio:.1f} (最佳 {best_ratio:.1f})"}

    def _score_quality(self, signals: list) -> Dict:
        """信号质量评分（加权 strength）"""
        total_weight = 0.0
        weighted_sum = 0.0

        for s in signals:
            w = STRATEGY_WEIGHTS.get(s.strategy, 0.15)
            weighted_sum += s.strength * w
            total_weight += w

        if total_weight == 0:
            return {"score": 0, "detail": "无有效信号"}

        # 归一化到 0-100
        # weighted_sum / total_weight 得到 0-1 的加权平均
        normalized = weighted_sum / total_weight
        score = round(normalized * 100)

        return {"score": score, "detail": f"加权强度 {normalized:.2f}"}

    def _score_volume(self, signals: list) -> Dict:
        """量能确认评分（从信号 reasons 中提取量比信息）"""
        volume_ratio = self._extract_volume_ratio(signals)

        if volume_ratio is None:
            return {"score": 40, "detail": "无量能数据"}
        elif volume_ratio < 1.0:
            return {"score": 20, "detail": f"缩量 (量比{volume_ratio:.1f})"}
        elif volume_ratio < 1.5:
            return {"score": 50, "detail": f"平量 (量比{volume_ratio:.1f})"}
        elif volume_ratio < 2.0:
            return {"score": 75, "detail": f"温和放量 (量比{volume_ratio:.1f})"}
        else:
            return {"score": 100, "detail": f"显著放量 (量比{volume_ratio:.1f})"}

    def _score_timeliness(self, sig_date: date, period_end: date) -> Dict:
        """信号时效评分"""
        days = (period_end - sig_date).days

        if days <= 1:
            score = 100
        elif days <= 3:
            score = 80
        elif days <= 5:
            score = 50
        else:
            score = 30

        return {"score": score, "detail": f"{days}天前"}

    def _extract_volume_ratio(self, signals: list) -> Optional[float]:
        """从信号 reasons 中提取量比数值"""
        for s in signals:
            if not s.reasons:
                continue
            try:
                reasons = json.loads(s.reasons) if isinstance(s.reasons, str) else s.reasons
            except (json.JSONDecodeError, TypeError):
                continue

            for r in reasons:
                text = r if isinstance(r, str) else str(r.get("detail", ""))
                # 匹配 "量比: 2.3" 或 "量比2.3" 或 "volume_ratio: 2.3"
                match = re.search(r'量比[:\s]*(\d+\.?\d*)', text)
                if match:
                    return float(match.group(1))
                match = re.search(r'volume.?ratio[:\s]*(\d+\.?\d*)', text, re.IGNORECASE)
                if match:
                    return float(match.group(1))

        return None

    def _score_strategy_backtest(self, signals: list, backtest_stats: Dict) -> Dict:
        """策略回测质量评分（Step 4: 新增第6维）

        评分逻辑：
        - 取信号涉及的所有策略中最好的回测表现
        - Sharpe > 1.0 → 100分 ; 0.5~1.0 → 70分 ; 0~0.5 → 40分 ; < 0 → 10分
        - win_rate < 40% → 额外 -20 分
        - total_trades < 30 → 50 分（样本不足，中性评估）
        """
        if not backtest_stats:
            return {"score": 50, "detail": "无回测数据"}

        strategies = list(set(s.strategy for s in signals if s.strategy))
        best_score = -1
        best_detail = "无匹配策略"

        for strat in strategies:
            stat = backtest_stats.get(strat)
            if not stat:
                continue

            sharpe = stat.get("sharpe_ratio")
            win_rate = stat.get("win_rate")
            total_trades = stat.get("total_trades")

            # 样本量不足，给中性分
            if total_trades is not None and total_trades < 30:
                score = 50
                detail = f"{strat} 样本不足({total_trades}笔)"
                if score > best_score:
                    best_score = score
                    best_detail = detail
                continue

            # 基于 Sharpe 评分
            if sharpe is None:
                score = 50
                detail = f"{strat} 无Sharpe数据"
            elif sharpe > 1.0:
                score = 100
                detail = f"{strat} Sharpe={sharpe:.2f}"
            elif sharpe > 0.5:
                score = 70
                detail = f"{strat} Sharpe={sharpe:.2f}"
            elif sharpe > 0:
                score = 40
                detail = f"{strat} Sharpe={sharpe:.2f}"
            else:
                score = 10
                detail = f"{strat} Sharpe={sharpe:.2f} ⚠️负值"

            # 胜率惩罚
            if win_rate is not None and win_rate < 40:
                score = max(0, score - 20)
                detail += f", 胜率{win_rate:.0f}%偏低"
            elif win_rate is not None:
                detail += f", 胜率{win_rate:.0f}%"

            if score > best_score:
                best_score = score
                best_detail = detail

        return {"score": max(best_score, 0) if best_score >= 0 else 50, "detail": best_detail}

    def _score_stock_quality(self, symbol: str, stock_analysis: Dict) -> Dict:
        """标的质量评分（基于庄股风险等级）

        risk_level=low → 100分, medium → 50分, high → 0分
        未传入 stock_analysis 时默认 70 分（向后兼容）
        """
        analysis = stock_analysis.get(symbol)
        if not analysis:
            return {"score": 70, "detail": "无标的质量数据"}

        risk_level = analysis.get("risk_level", "medium")
        score_map = {"low": 100, "medium": 50, "high": 0}
        score = score_map.get(risk_level, 70)

        label_map = {"low": "标的质量良好", "medium": "标的存在一定风险", "high": "庄股风险高"}
        detail = label_map.get(risk_level, "未知")

        risk_score = analysis.get("risk_score", 0)
        detail += f"(风险分{risk_score})"

        return {"score": score, "detail": detail}

"""
决策驾驶舱 — 数据聚合器

调用现有引擎收集五维原始数据并归一化为 0-100 分（50=中性）：
  技术面 / 基本面 / 新闻情感 / ML 预测 / 持仓风险
所有重操作（collect / predict）放线程池由路由层调度；本模块同步实现。
"""
from datetime import datetime
from typing import Dict, Optional, List

from loguru import logger

from advisor_engine.context_collector import AdvisorContextCollector
from data_engine.storage.database import get_session
from data_engine.storage.repository import FinancialRepository, ValuationRepository
from portfolio.calculator import PortfolioCalculator
from cockpit_engine.scorer import score_cockpit
from trading_engine.risk.adapter import build_broker_info, get_total_capital, get_max_position_pct
from trading_engine.position_sizing import size_position


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class CockpitAggregator:
    """决策驾驶舱聚合器"""

    def __init__(self) -> None:
        self._collector = AdvisorContextCollector()

    # ---------- 各维度评分 ----------

    @staticmethod
    def _score_technical(
        indicators: Optional[Dict],
        signals: Optional[List[Dict]] = None,
        latest_price: Optional[float] = None,
    ) -> (Optional[float], Dict):
        """技术面：综合「指标状态」多因子打分（均线排列/MACD/RSI/KDJ/BOLL），
        50=中性。只要有日线指标就几乎总能给分；新鲜交叉信号作为加分项叠加。
        """
        ind = indicators or {}

        def _num(key):
            v = ind.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        ma5, ma10, ma20, ma60 = _num("ma5"), _num("ma10"), _num("ma20"), _num("ma60")
        dif, dea, hist = _num("macd_dif"), _num("macd_dea"), _num("macd_hist")
        rsi = _num("rsi")
        k, d = _num("kdj_k"), _num("kdj_d")
        boll_u, boll_m, boll_l = _num("boll_upper"), _num("boll_middle"), _num("boll_lower")
        close = latest_price if isinstance(latest_price, (int, float)) else _num("ma5")

        sub_scores: List[float] = []
        factors: Dict[str, float] = {}

        # 1) 均线排列 / 趋势：多头排列偏高，空头偏低；价格相对 ma20 微调
        if ma5 is not None and ma10 is not None and ma20 is not None:
            s = 50.0
            if ma5 > ma10 > ma20:
                s = 75.0
                if ma60 is not None and ma20 > ma60:
                    s = 85.0
            elif ma5 < ma10 < ma20:
                s = 25.0
                if ma60 is not None and ma20 < ma60:
                    s = 15.0
            else:
                s = 50.0 + (10.0 if ma5 > ma20 else -10.0)
            if close is not None and ma20:
                s += _clamp((close - ma20) / ma20 * 100 * 1.5, -10, 10)  # 价距 ma20，每+1%≈+1.5
            s = _clamp(s, 0, 100)
            factors["trend_ma"] = round(s, 1)
            sub_scores.append(s)

        # 2) MACD：多头态(dif>dea) + 零轴上(dif>0) 偏高；柱状辅助
        if dif is not None and dea is not None:
            s = 60.0 if dif > dea else 40.0
            if dif > 0:
                s += 10.0
            elif dif < 0:
                s -= 10.0
            if hist is not None:
                s += 5.0 if hist > 0 else -5.0
            s = _clamp(s, 0, 100)
            factors["macd"] = round(s, 1)
            sub_scores.append(s)

        # 3) RSI：50 中性；偏多加分，但超买回调、超卖反弹
        if rsi is not None:
            if rsi >= 70:
                s = 60.0           # 超买：偏强但有回调风险，不给满分
            elif rsi <= 30:
                s = 40.0           # 超卖：偏弱但有反弹机会
            else:
                s = 50.0 + (rsi - 50.0) * 1.0  # 30~70 线性映射到 30~70
            s = _clamp(s, 0, 100)
            factors["rsi"] = round(s, 1)
            sub_scores.append(s)

        # 4) KDJ：k>d 偏多
        if k is not None and d is not None:
            s = _clamp(50.0 + (k - d) * 1.5, 0, 100)
            factors["kdj"] = round(s, 1)
            sub_scores.append(s)

        # 5) BOLL 位置：收盘价在通道内的相对位置（下轨0~上轨100）
        if close is not None and boll_u is not None and boll_l is not None and boll_u > boll_l:
            pos = (close - boll_l) / (boll_u - boll_l) * 100
            factors["boll_pos"] = round(_clamp(pos, 0, 100), 1)
            sub_scores.append(_clamp(pos, 0, 100))

        if not sub_scores:
            # 无任何指标可用（一般是没有日线数据）才判数据不足
            return None, {"available": False, "latest_signal": None}

        score = sum(sub_scores) / len(sub_scores)

        # 新鲜交叉信号：作为加分项小幅叠加（±最多 8 分），并保留信息到 detail
        latest_signal_type = None
        latest_strength = None
        latest_reasons = None
        if signals:
            latest = signals[-1]
            stype = (latest.get("signal_type") or "").upper()
            strength = float(latest.get("strength") or 0.0)
            if stype in ("BUY", "SELL"):
                latest_signal_type = stype
                latest_strength = round(strength, 2)
                latest_reasons = latest.get("reasons")
                score += (8.0 if stype == "BUY" else -8.0) * _clamp(strength, 0, 1)

        detail = {
            "available": True,
            "factors": factors,
            "latest_signal_type": latest_signal_type,
            "strength": latest_strength,
            "signal_count_20": len(signals) if signals else 0,
            "reasons": latest_reasons,
        }
        return round(_clamp(score, 0, 100), 1), detail

    @staticmethod
    def _score_fundamental(fin) -> (Optional[float], Dict):
        """基本面：ROE/净利率/营收增长/净利润增长/资产负债率 多因子打分平均"""
        if fin is None:
            return None, {"available": False}
        roe = fin.roe
        net_margin = fin.net_margin
        rev_yoy = fin.revenue_yoy
        np_yoy = fin.net_profit_yoy
        debt = fin.debt_ratio

        sub_scores = []
        if roe is not None:
            sub_scores.append(_clamp(50 + (roe - 8) * 3, 0, 100))        # ROE 8%中性，每+1%≈+3
        if net_margin is not None:
            sub_scores.append(_clamp(50 + (net_margin - 10) * 2, 0, 100))  # 净利率 10%中性
        if rev_yoy is not None:
            sub_scores.append(_clamp(50 + rev_yoy * 1.5, 0, 100))        # 营收增速 0中性
        if np_yoy is not None:
            sub_scores.append(_clamp(50 + np_yoy * 1.0, 0, 100))         # 净利增速 0中性
        if debt is not None:
            sub_scores.append(_clamp(50 + (50 - debt) * 1.0, 0, 100))    # 负债率 50%中性，越低越好

        if not sub_scores:
            return None, {"available": False}
        score = sum(sub_scores) / len(sub_scores)
        detail = {
            "available": True,
            "report_date": str(fin.report_date) if fin.report_date else None,
            "roe": roe, "net_margin": net_margin,
            "revenue_yoy": rev_yoy, "net_profit_yoy": np_yoy, "debt_ratio": debt,
        }
        return round(score, 1), detail

    @staticmethod
    def _score_sentiment(symbol: str, days: int = 7) -> (Optional[float], Dict):
        """新闻情感：实时抓取近 N 日新闻做 BERT 情绪聚合（无状态，不入库）。

        复用 news_engine.realtime.get_realtime_sentiment，口径与其一致：
        score = 50 + 50*(pos-neg)/total。抓不到/无新闻则 available=False，
        由 scorer 走缺失维度归一化。market 按 symbol 后缀自动推断（A股/港股/美股），
        避免港美股被误路由到 A 股新闻源。
        """
        from news_engine.realtime import get_realtime_sentiment
        from news_engine.news_scheduler import _infer_market

        r = get_realtime_sentiment(symbol, days=days, market=_infer_market(symbol))
        if not r.get("available"):
            return None, {"available": False, "reason": r.get("reason"),
                          "article_count": r.get("article_count", 0)}
        detail = {"available": True, "positive": r["positive"], "negative": r["negative"],
                  "neutral": r["neutral"], "total": r["total"], "days": days}
        return r["score"], detail

    @staticmethod
    def _score_ml(symbol: str) -> (Optional[float], Dict):
        """ML 预测：方向 × 置信度。无模型/数据不足则降级为缺失"""
        try:
            from prediction_engine.engine import PredictionEngine
            pred = PredictionEngine().predict(symbol)
        except FileNotFoundError:
            return None, {"available": False, "reason": "无训练模型"}
        except ValueError as e:
            return None, {"available": False, "reason": f"数据不足: {e}"}
        except Exception as e:
            logger.debug(f"ML 预测失败 {symbol}: {e}")
            return None, {"available": False, "reason": "预测异常"}

        direction = (pred.get("direction") or "").upper()
        conf = float(pred.get("confidence") or 0.0)
        if direction == "UP":
            score = 50 + 50 * conf
        elif direction == "DOWN":
            score = 50 - 50 * conf
        else:
            return None, {"available": False}
        detail = {
            "available": True,
            "direction": direction,
            "confidence": round(conf, 2),
            "predicted_return": pred.get("predicted_return"),
            "forward_days": pred.get("forward_days"),
        }
        return round(_clamp(score, 0, 100), 1), detail

    @staticmethod
    def _score_position(symbol: str, latest_price: Optional[float]) -> (Optional[float], Dict):
        """持仓风险：未持有→中性 50；持有→按浮盈亏映射健康度"""
        try:
            positions = PortfolioCalculator().get_current_positions()
        except Exception:
            return 50.0, {"holding": False}
        pos = next((p for p in positions if p.get("symbol") == symbol), None)
        if not pos:
            return 50.0, {"holding": False}

        avg_cost = pos.get("avg_cost")
        qty = pos.get("quantity")
        # 优先用实时价重算浮盈亏，否则用持仓自带（EOD）
        pnl_pct = pos.get("unrealized_pnl_pct")
        if latest_price and avg_cost:
            pnl_pct = (latest_price - avg_cost) / avg_cost * 100
        if pnl_pct is None:
            return 50.0, {"holding": True, "quantity": qty, "avg_cost": avg_cost}
        score = _clamp(50 + pnl_pct * 2.5, 0, 100)  # -20%..+20% → 0..100
        detail = {
            "holding": True, "quantity": qty, "avg_cost": avg_cost,
            "current_price": latest_price or pos.get("current_price"),
            "unrealized_pnl_pct": round(pnl_pct, 2),
            "market_value": pos.get("market_value"),
        }
        return round(score, 1), detail

    # ---------- 主入口 ----------

    def aggregate(self, symbol: str, light: bool = False) -> Dict:
        """聚合单只股票的五维分 + 综合建议（同步，调用方应放线程池）。

        Args:
            symbol: 股票代码
            light: 轻量档。跳过最慢的「新闻情感 + ML」两维（走网络/推理），
                只用技术+基本面+持仓三维，scorer 会对缺失维度自动重新归一化权重。
                批量选股(recommend_stocks)用它把单只耗时从 ~30s 降到亚秒级。
        """
        ctx = self._collector.collect(symbol, enable_web_search=False)
        name = ctx.get("name") or symbol
        price_data = ctx.get("price_data") or {}
        latest = price_data.get("latest") or {}
        latest_price = latest.get("close") if isinstance(latest, dict) else None
        dynamic_levels = ctx.get("dynamic_levels")

        # 基本面 / 估值（直接读库）
        session = get_session()
        try:
            fin_rows = FinancialRepository(session).get_financial_data(symbol, limit=1)
            fin = fin_rows[0] if fin_rows else None
            val = ValuationRepository(session).get_latest(symbol)
            valuation = None
            if val:
                valuation = {"pe": val.pe, "pe_ttm": val.pe_ttm, "pb": val.pb,
                             "total_mv": val.total_mv, "circ_mv": val.circ_mv}
            tech_score, tech_detail = self._score_technical(
                ctx.get("indicators"), ctx.get("signals"), latest_price)
            fund_score, fund_detail = self._score_fundamental(fin)
        finally:
            session.close()

        if light:
            senti_score, senti_detail = None, {"skipped": "light 档跳过新闻情感"}
            ml_score, ml_detail = None, {"skipped": "light 档跳过 ML 预测"}
        else:
            senti_score, senti_detail = self._score_sentiment(symbol)
            ml_score, ml_detail = self._score_ml(symbol)
        pos_score, pos_detail = self._score_position(symbol, latest_price)

        dimensions = {
            "technical": tech_score,
            "fundamental": fund_score,
            "sentiment": senti_score,
            "ml": ml_score,
            "position": pos_score,
        }

        # ---- 资金量：以真实总资金 + 当前持仓占比 + 集中度上限为基准 ----
        total_capital = get_total_capital()
        max_pct = get_max_position_pct()
        broker_info = build_broker_info(total_capital)
        held = broker_info.get("positions", {}).get(symbol) or {}
        held_value = float(held.get("market_value") or 0.0)
        current_pct = round(held_value / total_capital * 100, 1) if total_capital else 0.0

        scored = score_cockpit(dimensions, dynamic_levels, current_pct, max_pct)

        # 把「建议加仓 %」按真实资金换算成可执行金额/股数（硬约束：现金 + 风控）
        sizing = size_position(
            symbol, latest_price, scored.get("suggested_add_pct", 0.0),
            broker_info=broker_info, total_capital=total_capital, max_position_pct=max_pct,
        )

        return {
            "symbol": symbol,
            "name": name,
            "as_of": datetime.now().isoformat(timespec="seconds"),
            "price": {
                "latest": latest_price,
                "change_5d_pct": price_data.get("change_5d_pct"),
                "change_20d_pct": price_data.get("change_20d_pct"),
            },
            "valuation": valuation,
            "dynamic_levels": dynamic_levels,
            "total_capital": round(total_capital, 2),
            "available_cash": round(float(broker_info.get("cash") or 0.0), 2),
            "current_position": {
                "shares": held.get("quantity", 0),
                "value": round(held_value, 2),
                "pct": current_pct,
            },
            "suggested": sizing,
            "dimensions": {
                "technical": {"score": tech_score, "detail": tech_detail},
                "fundamental": {"score": fund_score, "detail": fund_detail},
                "sentiment": {"score": senti_score, "detail": senti_detail},
                "ml": {"score": ml_score, "detail": ml_detail},
                "position": {"score": pos_score, "detail": pos_detail},
            },
            **scored,
        }

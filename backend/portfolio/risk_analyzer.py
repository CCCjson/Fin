"""
组合风险分析器 — 计算投资组合层面的风控指标

指标体系：
1. 组合 Beta（vs 沪深300）
2. 行业集中度（HHI + Top3 占比）
3. 平均相关性（持仓间收益率相关系数）
4. 组合最大回撤（加权净值曲线）
5. 流动性风险（清仓所需天数）

综合风险评级：低 / 中 / 高
"""
import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import Dict, List, Optional
from loguru import logger

from sqlalchemy.exc import SQLAlchemyError

from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote, StockInfo


class PortfolioRiskAnalyzer:
    """投资组合风险分析器"""

    def analyze(
        self,
        positions: List[Dict],
        lookback_days: int = 60,
        as_of_date: Optional[date] = None,
    ) -> Dict:
        """
        分析持仓组合的整体风险

        Args:
            positions: 持仓列表，每项包含 symbol, market_value, quantity 等
            lookback_days: 回溯天数（默认60个交易日）
            as_of_date: 截止日期，默认今天

        Returns:
            包含 5 个维度指标 + 综合评级的字典
        """
        if not positions:
            return {"error": "无持仓", "risk_level": "N/A"}

        if as_of_date is None:
            as_of_date = date.today()

        start_date = as_of_date - timedelta(days=lookback_days * 2)  # 多取一些以确保有足够交易日

        # 计算每只持仓的权重
        total_mv = sum(p.get("market_value", 0) or 0 for p in positions)
        if total_mv <= 0:
            return {"error": "持仓市值为0", "risk_level": "N/A"}

        symbols = [p["symbol"] for p in positions if p.get("symbol")]
        weights = {
            p["symbol"]: (p.get("market_value", 0) or 0) / total_mv
            for p in positions if p.get("symbol")
        }

        result: Dict = {
            "total_market_value": round(total_mv, 2),
            "position_count": len(symbols),
        }

        session = get_session()
        try:
            # 获取持仓股价格矩阵
            price_matrix = self._get_price_matrix(session, symbols, start_date, as_of_date)

            # 1. 组合 Beta
            result["beta"] = self._calc_portfolio_beta(price_matrix, weights, start_date, as_of_date)

            # 2. 行业集中度
            result["industry_concentration"] = self._calc_industry_concentration(positions, weights)

            # 3. 平均相关性
            result["correlation"] = self._calc_avg_correlation(price_matrix, symbols)

            # 4. 组合最大回撤
            result["max_drawdown"] = self._calc_portfolio_drawdown(price_matrix, weights)

            # 5. 流动性风险
            result["liquidity"] = self._calc_liquidity_risk(session, positions, start_date, as_of_date)

        except Exception as e:
            logger.warning(f"组合风险分析失败: {e}")
            result["error"] = str(e)
        finally:
            session.close()

        # 综合风险评级
        result["risk_level"] = self._calc_overall_risk_level(result)

        logger.info(f"组合风险分析完成: {len(symbols)} 只持仓, "
                    f"Beta={result.get('beta', {}).get('value', 'N/A')}, "
                    f"风险评级={result['risk_level']}")

        return result

    # ------------------------------------------------------------------
    # 1. 组合 Beta
    # ------------------------------------------------------------------
    def _calc_portfolio_beta(
        self, price_matrix: pd.DataFrame, weights: Dict[str, float],
        start_date: date, as_of_date: date
    ) -> Dict:
        """计算组合相对沪深300的 Beta"""
        try:
            import akshare as ak
            from net import domestic_akshare
            # 获取沪深300日线（统一网络层）
            index_df = domestic_akshare(ak.stock_zh_index_daily_em, symbol="sh000300")
            index_df["date"] = pd.to_datetime(index_df["date"]).dt.date
            index_df = index_df[(index_df["date"] >= start_date) & (index_df["date"] <= as_of_date)]
            index_df = index_df.sort_values("date").set_index("date")
            index_returns = index_df["close"].pct_change().dropna()

            if price_matrix.empty or len(index_returns) < 10:
                return {"value": None, "status": "数据不足"}

            # 计算加权组合收益率
            returns_df = price_matrix.pct_change().dropna()
            portfolio_returns = pd.Series(0.0, index=returns_df.index)
            for sym, w in weights.items():
                if sym in returns_df.columns:
                    portfolio_returns += returns_df[sym] * w

            # 对齐日期
            common_dates = portfolio_returns.index.intersection(index_returns.index)
            if len(common_dates) < 10:
                return {"value": None, "status": "重叠交易日不足"}

            pr = portfolio_returns.loc[common_dates]
            ir = index_returns.loc[common_dates]

            cov = np.cov(pr.values, ir.values)[0][1]
            var = np.var(ir.values)
            beta = round(cov / var, 2) if var > 0 else None

            return {
                "value": beta,
                "benchmark": "沪深300",
                "interpretation": (
                    "高弹性（进攻型）" if beta and beta > 1.2
                    else "中性" if beta and 0.8 <= beta <= 1.2
                    else "防御型" if beta and beta < 0.8
                    else "N/A"
                ),
                "trading_days": len(common_dates),
            }
        except Exception as e:
            logger.warning(f"Beta 计算失败: {e}")
            return {"value": None, "status": f"计算失败: {e}"}

    # ------------------------------------------------------------------
    # 2. 行业集中度
    # ------------------------------------------------------------------
    @staticmethod
    def _load_industries(symbols: List[str]) -> Dict[str, str]:
        """一次查库拿到 `symbol -> industry`。查不到/为空的不进 dict。"""
        if not symbols:
            return {}
        try:
            session = get_session()
            try:
                rows = (session.query(StockInfo.symbol, StockInfo.industry)
                        .filter(StockInfo.symbol.in_(symbols)).all())
            finally:
                session.close()
            return {s: i for s, i in rows if i}
        except SQLAlchemyError as e:
            logger.warning(f"读取行业分类失败，全部记为「未知」: {e}")
            return {}

    def _calc_industry_concentration(self, positions: List[Dict], weights: Dict[str, float]) -> Dict:
        """计算行业集中度。行业分类**只读本地库**（`StockInfo.industry`），零出网。

        此前逐只持仓调 `domestic_akshare(ak.stock_individual_info_em)`。该 akshare
        接口打的是 `push2.eastmoney.com/api/qt/stock/get`——**该端点已被东财封杀**
        （2026-07-10 实测：同一 Session 同一 IP 上 `ulist.np/get` 返 200，它连 TLS
        都建不起来）。而 `domestic_akshare` 把「端点死了」当成「IP 死了」，于是每只
        持仓白烧 `max_rounds - 1` 个快代理 IP，异常再被 `except Exception` 吞成
        「未知」——**烧了 IP，一个行业都没查到**。

        行业数据现由 `data_engine.industry_updater` 从东财 `clist/get` 的 f100
        字段全市场回填进 `StockInfo.industry`（那个端点是活的）。库里没有就是「未知」。
        """
        industry_map: Dict[str, str] = {}
        industry_weights: Dict[str, float] = {}

        symbols = [p.get("symbol", "") for p in positions if p.get("symbol")]
        from_db = self._load_industries(symbols)

        for p in positions:
            sym = p.get("symbol", "")
            if not sym:
                continue
            # 调用方自带的行业 > 本地库 > 未知
            industry = p.get("industry") or from_db.get(sym) or "未知"

            industry_map[sym] = industry
            w = weights.get(sym, 0)
            industry_weights[industry] = industry_weights.get(industry, 0) + w

        if not industry_weights:
            return {"concentrated": False, "status": "无行业数据"}

        # 按权重排序
        sorted_industries = sorted(industry_weights.items(), key=lambda x: x[1], reverse=True)
        top3 = sorted_industries[:3]
        top3_pct = round(sum(w for _, w in top3) * 100, 1)

        # HHI 指数（赫芬达尔指数，0-10000）
        hhi = round(sum((w * 100) ** 2 for w in industry_weights.values()), 0)

        return {
            "industries": {ind: round(w * 100, 1) for ind, w in sorted_industries},
            "top3": [{"industry": ind, "pct": round(w * 100, 1)} for ind, w in top3],
            "top3_pct": top3_pct,
            "hhi": hhi,
            "concentrated": top3_pct > 80,
            "interpretation": (
                "高度集中，分散化不足" if top3_pct > 80
                else "适度集中" if top3_pct > 60
                else "分散化良好"
            ),
        }

    # ------------------------------------------------------------------
    # 3. 平均相关性
    # ------------------------------------------------------------------
    def _calc_avg_correlation(self, price_matrix: pd.DataFrame, symbols: List[str]) -> Dict:
        """计算持仓间收益率的平均相关性"""
        if price_matrix.empty or len(price_matrix.columns) < 2:
            return {"value": None, "status": "持仓不足2只，无法计算相关性"}

        returns_df = price_matrix.pct_change().dropna()
        if len(returns_df) < 5:
            return {"value": None, "status": "交易日数据不足"}

        corr_matrix = returns_df.corr()

        # 取上三角（不含对角线）的平均值
        n = len(corr_matrix)
        upper_values = []
        for i in range(n):
            for j in range(i + 1, n):
                val = corr_matrix.iloc[i, j]
                if not np.isnan(val):
                    upper_values.append(val)

        if not upper_values:
            return {"value": None, "status": "无有效相关系数"}

        avg_corr = round(np.mean(upper_values), 2)

        return {
            "value": avg_corr,
            "diversified": avg_corr < 0.7,
            "interpretation": (
                "高度相关，分散化不足" if avg_corr > 0.7
                else "中度相关" if avg_corr > 0.4
                else "低相关，分散化良好"
            ),
        }

    # ------------------------------------------------------------------
    # 4. 组合最大回撤
    # ------------------------------------------------------------------
    def _calc_portfolio_drawdown(self, price_matrix: pd.DataFrame, weights: Dict[str, float]) -> Dict:
        """计算加权组合净值曲线的历史最大回撤"""
        if price_matrix.empty:
            return {"value": None, "status": "无价格数据"}

        returns_df = price_matrix.pct_change().dropna()
        if returns_df.empty:
            return {"value": None, "status": "无收益率数据"}

        # 计算加权组合日收益率
        portfolio_returns = pd.Series(0.0, index=returns_df.index)
        for sym, w in weights.items():
            if sym in returns_df.columns:
                portfolio_returns += returns_df[sym] * w

        # 计算累计净值
        cum_returns = (1 + portfolio_returns).cumprod()

        # 计算最大回撤
        running_max = cum_returns.expanding().max()
        drawdown = (cum_returns - running_max) / running_max
        max_dd = round(drawdown.min() * 100, 2)
        max_dd_date = str(drawdown.idxmin()) if not drawdown.empty else "N/A"

        return {
            "value": max_dd,
            "max_dd_date": max_dd_date,
            "interpretation": (
                "回撤较大，需关注" if max_dd < -15
                else "回撤适中" if max_dd < -8
                else "回撤控制良好"
            ),
        }

    # ------------------------------------------------------------------
    # 5. 流动性风险
    # ------------------------------------------------------------------
    def _calc_liquidity_risk(
        self, session, positions: List[Dict],
        start_date: date, as_of_date: date
    ) -> Dict:
        """评估每只持仓的流动性风险（清仓所需天数）"""
        liquidity_risks = []

        for p in positions:
            sym = p.get("symbol", "")
            mv = p.get("market_value", 0) or 0
            if not sym or mv <= 0:
                continue

            # 查询近期日均成交额
            quotes = session.query(DailyQuote).filter(
                DailyQuote.symbol == sym,
                DailyQuote.date >= start_date,
                DailyQuote.date <= as_of_date,
            ).all()

            if not quotes:
                liquidity_risks.append({
                    "symbol": sym,
                    "name": p.get("name", sym),
                    "days_to_clear": None,
                    "risk": "unknown",
                })
                continue

            # 日均成交额 = volume * close 的平均值
            daily_amounts = []
            for q in quotes:
                if q.volume and q.close:
                    daily_amounts.append(q.volume * q.close)

            if not daily_amounts:
                liquidity_risks.append({
                    "symbol": sym,
                    "name": p.get("name", sym),
                    "days_to_clear": None,
                    "risk": "unknown",
                })
                continue

            avg_daily_amount = np.mean(daily_amounts)
            days_to_clear = round(mv / avg_daily_amount, 2) if avg_daily_amount > 0 else 999

            risk_level = "high" if days_to_clear > 0.5 else "low"

            liquidity_risks.append({
                "symbol": sym,
                "name": p.get("name", sym),
                "market_value": round(mv, 0),
                "avg_daily_amount": round(avg_daily_amount, 0),
                "days_to_clear": days_to_clear,
                "risk": risk_level,
            })

        high_risk = [r for r in liquidity_risks if r["risk"] == "high"]

        return {
            "positions": liquidity_risks,
            "high_risk_count": len(high_risk),
            "has_liquidity_risk": len(high_risk) > 0,
            "interpretation": (
                f"{len(high_risk)} 只持仓流动性不足" if high_risk
                else "所有持仓流动性良好"
            ),
        }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _get_price_matrix(
        self, session, symbols: List[str],
        start_date: date, as_of_date: date
    ) -> pd.DataFrame:
        """获取多只股票的收盘价矩阵（columns=symbols, index=date）"""
        if not symbols:
            return pd.DataFrame()

        quotes = session.query(
            DailyQuote.symbol, DailyQuote.date, DailyQuote.close
        ).filter(
            DailyQuote.symbol.in_(symbols),
            DailyQuote.date >= start_date,
            DailyQuote.date <= as_of_date,
        ).all()

        if not quotes:
            return pd.DataFrame()

        data = [(q.symbol, q.date, q.close) for q in quotes]
        df = pd.DataFrame(data, columns=["symbol", "date", "close"])
        pivot = df.pivot_table(index="date", columns="symbol", values="close")
        pivot = pivot.sort_index().ffill()

        return pivot

    def _calc_overall_risk_level(self, result: Dict) -> str:
        """根据各项指标综合评定风险等级"""
        risk_score = 0

        # Beta
        beta = result.get("beta", {}).get("value")
        if beta is not None:
            if beta > 1.3:
                risk_score += 2
            elif beta > 1.1:
                risk_score += 1

        # 行业集中度
        concentrated = result.get("industry_concentration", {}).get("concentrated", False)
        if concentrated:
            risk_score += 2

        # 相关性
        corr = result.get("correlation", {}).get("value")
        if corr is not None and corr > 0.7:
            risk_score += 2
        elif corr is not None and corr > 0.5:
            risk_score += 1

        # 最大回撤
        dd = result.get("max_drawdown", {}).get("value")
        if dd is not None and dd < -15:
            risk_score += 2
        elif dd is not None and dd < -8:
            risk_score += 1

        # 流动性
        has_liq_risk = result.get("liquidity", {}).get("has_liquidity_risk", False)
        if has_liq_risk:
            risk_score += 1

        if risk_score >= 5:
            return "高"
        elif risk_score >= 3:
            return "中"
        else:
            return "低"

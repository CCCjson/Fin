"""
AI 投资顾问 — 数据收集器
收集单只股票的全维度本地数据，供 prompt 使用。
"""
import traceback
from datetime import datetime, timedelta
from typing import Dict, Optional

import pandas as pd
from loguru import logger

from data_engine.engine import DataEngine
from data_engine.storage.database import get_session
from data_engine.storage.repository import StockRepository
from data_engine.storage.history_repository import HistoryRepository
from strategy.indicators import TechnicalIndicators
from strategy.signal_generator import SignalGenerator
from analysis_engine.engine import AnalysisEngine


class AdvisorContextCollector:
    """收集单只股票的全维度数据"""

    @staticmethod
    def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
        """统一 DataFrame 格式：确保 date 是列而非 index，使用整数 RangeIndex"""
        df = df.copy()
        if "date" not in df.columns and df.index.name == "date":
            df = df.reset_index()
        if "date" in df.columns:
            df = df.sort_values("date").reset_index(drop=True)
        return df

    def collect(self, symbol: str, enable_web_search: bool = False) -> Dict:
        """
        收集股票数据，每项独立 try/except，部分失败不影响整体。

        Args:
            symbol: 股票代码
            enable_web_search: 是否搜索网络新闻

        Returns:
            结构化数据字典
        """
        now = datetime.now()
        end_date = now.strftime("%Y-%m-%d")
        start_date = (now - timedelta(days=120)).strftime("%Y-%m-%d")

        result: Dict = {
            "symbol": symbol,
            "name": "",
            "collected_at": now.isoformat(),
            "price_data": None,
            "indicators": None,
            "signals": None,
            "patterns": None,
            "signal_stats": None,
            "fundamentals": None,
            "web_news": None,
            "manipulation_risk": None,
            "small_cap_profile": None,
            "dynamic_levels": None,
            "market_behavior": None,
        }

        # 查股票名称
        session = None
        try:
            session = get_session()
            stock_repo = StockRepository(session)
            info = stock_repo.get_stock_info(symbol)
            if info:
                result["name"] = info.name
        except Exception:
            logger.warning(f"获取股票名称失败: {symbol}")
        finally:
            if session:
                session.close()

        # 获取行情数据
        df: Optional[pd.DataFrame] = None
        engine: Optional[DataEngine] = None
        try:
            engine = DataEngine()
            raw_df = engine.get_daily_data(symbol, start_date, end_date, db_only=True)
            if raw_df is not None and not raw_df.empty:
                df = self._normalize_df(raw_df)
                result["price_data"] = self._extract_price_data(df)
            else:
                logger.warning(f"无行情数据: {symbol}")
        except Exception:
            logger.warning(f"获取行情数据失败: {symbol}\n{traceback.format_exc()}")
        finally:
            if engine:
                engine.close()

        if df is None or df.empty:
            return result

        # 计算技术指标
        try:
            df_ind = TechnicalIndicators.calculate_all_indicators(df)
            result["indicators"] = self._extract_indicators(df_ind)
        except Exception:
            logger.warning(f"计算技术指标失败: {symbol}\n{traceback.format_exc()}")

        # StockAnalyzer 深度分析（用已有的 df 构建 analysis_quotes）
        try:
            from report_engine.stock_analyzer import StockAnalyzer
            analysis_quotes = []
            for _, row in df.tail(20).iterrows():
                analysis_quotes.append({
                    "date": str(row.get("date", ""))[:10],
                    "open": float(row["open"]) if pd.notna(row["open"]) else 0,
                    "high": float(row["high"]) if pd.notna(row["high"]) else 0,
                    "low": float(row["low"]) if pd.notna(row["low"]) else 0,
                    "close": float(row["close"]) if pd.notna(row["close"]) else 0,
                    "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
                    "turnover": float(row["turnover"]) if "turnover" in row and pd.notna(row.get("turnover")) else None,
                })
            if len(analysis_quotes) >= 5:
                atr_val = result.get("indicators", {}).get("atr") if result.get("indicators") else None
                latest_close = analysis_quotes[-1]["close"] if analysis_quotes else None

                try:
                    result["manipulation_risk"] = StockAnalyzer.analyze_manipulation_risk(
                        analysis_quotes, result.get("fundamentals"))
                except Exception:
                    pass
                try:
                    result["small_cap_profile"] = StockAnalyzer.analyze_small_cap_profile(
                        analysis_quotes, result.get("fundamentals"))
                except Exception:
                    pass
                try:
                    result["dynamic_levels"] = StockAnalyzer.calculate_dynamic_levels(
                        latest_close, analysis_quotes, atr_val, "BUY")
                except Exception:
                    pass
                try:
                    result["market_behavior"] = StockAnalyzer.analyze_market_behavior(analysis_quotes)
                except Exception:
                    pass
        except Exception:
            logger.debug(f"StockAnalyzer 深度分析失败: {symbol}\n{traceback.format_exc()}")

        # 策略信号
        gen: Optional[SignalGenerator] = None
        try:
            gen = SignalGenerator()
            signals = gen.generate_signals_for_symbol(
                symbol, start_date, end_date, save_to_db=False, db_only=True
            )
            result["signals"] = [s.to_dict() for s in signals[-20:]]
        except Exception:
            logger.warning(f"生成策略信号失败: {symbol}\n{traceback.format_exc()}")
        finally:
            if gen:
                try:
                    gen.engine.close()
                except Exception:
                    pass
                try:
                    gen.repo.close()
                except Exception:
                    pass

        # K线形态（df 已经过 _normalize_df，使用整数 RangeIndex）
        try:
            ae = AnalysisEngine()
            patterns = ae.detect_patterns(df)
            result["patterns"] = self._summarize_patterns(patterns, df)
        except Exception:
            logger.warning(f"检测K线形态失败: {symbol}\n{traceback.format_exc()}")

        # 信号统计
        repo: Optional[HistoryRepository] = None
        try:
            repo = HistoryRepository()
            stats = repo.get_signal_statistics(symbol=symbol, days=90)
            result["signal_stats"] = stats
        except Exception:
            logger.warning(f"获取信号统计失败: {symbol}\n{traceback.format_exc()}")
        finally:
            if repo:
                repo.close()

        # 联网搜索（可选）：个股新闻 + 基本面数据
        if enable_web_search:
            try:
                from report_engine.web_searcher import MarketWebSearcher
                searcher = MarketWebSearcher(random_ip=True)

                # 搜索个股相关新闻（用股票代码或名称搜索）
                search_keyword = result["name"] or symbol.split(".")[0]
                news = searcher.search_stock_news(search_keyword, limit=10)
                result["web_news"] = news if news else None
            except Exception:
                logger.warning(f"搜索个股新闻失败: {symbol}\n{traceback.format_exc()}")

            try:
                from report_engine.web_searcher import MarketWebSearcher
                searcher = MarketWebSearcher(random_ip=True)

                # 获取基本面数据（PE/PB/市值/ROE）
                fundamentals = searcher.fetch_stock_fundamentals([symbol])
                if fundamentals and symbol in fundamentals:
                    result["fundamentals"] = fundamentals[symbol]
            except Exception:
                logger.warning(f"获取基本面数据失败: {symbol}\n{traceback.format_exc()}")

        return result

    # ---------- 数据提取辅助 ----------

    @staticmethod
    def _extract_price_data(df: pd.DataFrame) -> Dict:
        """从 DataFrame 提取行情摘要（df 已经过 _normalize_df）"""
        latest = df.iloc[-1]

        recent_10 = df.tail(10)
        recent_list = []
        for _, row in recent_10.iterrows():
            d = str(row.get("date", ""))[:10]
            vol = int(row["volume"]) if pd.notna(row["volume"]) else 0
            recent_list.append({
                "date": d,
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": vol,
            })

        def _pct_change(days: int) -> Optional[float]:
            if len(df) < days + 1:
                return None
            old_close = float(df.iloc[-(days + 1)]["close"])
            if old_close == 0:
                return None
            return round((float(latest["close"]) - old_close) / old_close * 100, 2)

        last_20 = df.tail(20)
        return {
            "latest": recent_list[-1] if recent_list else None,
            "recent_10d": recent_list,
            "change_5d_pct": _pct_change(5),
            "change_20d_pct": _pct_change(20),
            "change_60d_pct": _pct_change(60),
            "high_20d": round(float(last_20["high"].max()), 2),
            "low_20d": round(float(last_20["low"].min()), 2),
        }

    @staticmethod
    def _extract_indicators(df: pd.DataFrame) -> Dict:
        """提取最新一行的技术指标"""
        last = df.iloc[-1]

        def _val(col: str) -> Optional[float]:
            v = last.get(col)
            if v is None or pd.isna(v):
                return None
            return round(float(v), 4)

        return {
            "ma5": _val("ma5"),
            "ma10": _val("ma10"),
            "ma20": _val("ma20"),
            "ma60": _val("ma60"),
            "macd_dif": _val("macd_dif"),
            "macd_dea": _val("macd_dea"),
            "macd_hist": _val("macd"),
            "rsi": _val("rsi"),
            "kdj_k": _val("kdj_k"),
            "kdj_d": _val("kdj_d"),
            "kdj_j": _val("kdj_j"),
            "boll_upper": _val("boll_upper"),
            "boll_middle": _val("boll_mid"),
            "boll_lower": _val("boll_lower"),
            "atr": _val("atr"),
            "volume_ratio": _val("volume_ratio"),
        }

    # K线形态英中翻译
    PATTERN_NAMES = {
        "doji": "十字星",
        "hammer": "锤子线",
        "shooting_star": "射击之星",
        "hanging_man": "上吊线",
        "engulfing_bullish": "看涨吞没",
        "engulfing_bearish": "看跌吞没",
        "morning_star": "早晨之星",
        "evening_star": "黄昏之星",
        "three_white_soldiers": "三连阳（红三兵）",
        "three_black_crows": "三连阴（黑三鸦）",
        "dark_cloud_cover": "乌云盖顶",
        "piercing_line": "刺透形态",
    }

    @staticmethod
    def _summarize_patterns(patterns: Dict, df: pd.DataFrame) -> list:
        """将形态检测结果转为可读描述（df 已经过 _normalize_df，RangeIndex + date 列）"""
        results = []
        n = len(df)
        for name, indices in patterns.items():
            for idx in indices:
                # 确保 idx 是整数位置索引（跳过非 int 类型，如意外的 Timestamp）
                if not isinstance(idx, (int,)):
                    continue
                # 只取最近 10 个交易日内识别到的形态
                if idx < n - 10 or idx >= n:
                    continue
                d = str(df.iloc[idx]["date"])[:10] if "date" in df.columns else ""
                cn_name = AdvisorContextCollector.PATTERN_NAMES.get(name, name)
                results.append({"pattern": cn_name, "date": d})
        return results

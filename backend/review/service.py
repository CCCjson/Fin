"""
每日复盘服务 — 聚合当日数据 + AI 评分
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

from loguru import logger

from common.market import A_SHARE
from common.market_time import market_day_bounds, market_today
from dotenv import load_dotenv

from data_engine.storage.database import get_session
from data_engine.storage.models import (
    DailyQuote, ManualTrade, Signal, DailyReview,
    StockInfo, RealtimeSnapshot, PendingOrder,
)
from portfolio.calculator import PortfolioCalculator
from llm_config import get_cheap_model, normalize_chat_params

# 加载 .env
_BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(_BACKEND_DIR / ".env", override=True)

# 大盘指数映射（EastMoney secid -> 显示信息）
INDEX_MAP = {
    "000001": {"name": "上证指数", "secid": "1.000001"},
    "399001": {"name": "深证成指", "secid": "0.399001"},
    "399006": {"name": "创业板指", "secid": "0.399006"},
    "HSI":    {"name": "恒生指数", "secid": None},
    "SPX":    {"name": "标普500",  "secid": None},
}

# 小白复盘模板
BEGINNER_TEMPLATE = """## 今日复盘

### 一、今天做了什么操作？为什么？
<!-- 记录你今天的买卖操作，以及当时的思考过程 -->


### 二、哪笔操作最满意？为什么？
<!-- 回顾你今天做得最好的一个决定 -->


### 三、哪笔操作最后悔？下次怎么改？
<!-- 诚实面对失误，写下改进方法 -->


### 四、有没有管住手？
- [ ] 今天没有冲动交易
- [ ] 每笔交易都设了止损
- [ ] 单股仓位没有超过 20%

### 五、今天学到了什么？
<!-- 一句话总结今天最大的收获 -->


### 六、明天计划
- 关注标的:
- 计划操作:
- 需要注意:
"""


class ReviewService:
    """每日复盘服务"""

    def get_review_data(self, review_date: date,
                        signals_limit: int = 20, signals_offset: int = 0) -> Dict[str, Any]:
        """
        聚合指定日期的全部复盘数据

        返回: 大盘指数、当日持仓表现、当日交易、当日信号（分页）、复盘笔记+评分
        """
        session = get_session()
        try:
            # 1. 大盘指数
            indices_data = self._get_indices(session, review_date)
            indices = indices_data["items"]

            # 2. 持仓当日表现
            positions = self._get_positions_daily(session, review_date)

            # 3. 当日交易
            day_trades = self._get_day_trades(session, review_date)

            # 4. 当日信号（分页）
            signals_total = session.query(Signal).filter(Signal.date == review_date).count()
            day_signals = self._get_day_signals(session, review_date, signals_limit, signals_offset)

            # 5. 当日自动化决策
            decisions = self._get_day_decisions(session, review_date)
            decisions_filled = sum(1 for d in decisions if d["status"] == "FILLED")
            decisions_rejected = sum(1 for d in decisions if d["status"] == "REJECTED")
            decisions_expired = sum(1 for d in decisions if d["status"] == "EXPIRED")
            decisions_failed = sum(1 for d in decisions if d["status"] == "FAILED")

            # 6. 当日盈亏汇总
            daily_pnl = sum(p.get("daily_pnl", 0) or 0 for p in positions)

            # 整体日盈亏百分比（加权）
            total_base = 0.0
            for p in positions:
                pnl = p.get("daily_pnl") or 0
                pct = p.get("daily_pnl_pct")
                if pct is not None and pct != 0:
                    total_base += abs(pnl / pct * 100)
                elif pnl == 0 and pct is not None:
                    # pnl=0 但有 base_value
                    pass
            daily_pnl_pct = round(daily_pnl / total_base * 100, 2) if total_base > 0 else None

            # 6. 复盘记录（笔记 + 评分）
            review = session.query(DailyReview).filter(
                DailyReview.review_date == review_date
            ).first()

            review_data = None
            if review:
                # 解析维度评分 JSON
                dim_scores = None
                if review.ai_dimension_scores:
                    try:
                        dim_scores = json.loads(review.ai_dimension_scores)
                    except json.JSONDecodeError:
                        pass

                review_data = {
                    "id": review.id,
                    "review_date": str(review.review_date),
                    "self_score": review.self_score,
                    "ai_score": review.ai_score,
                    "ai_score_reason": review.ai_score_reason,
                    "ai_dimension_scores": dim_scores,
                    "composite_score": review.composite_score,
                    "note": review.note,
                    "template_used": review.template_used,
                    "updated_at": str(review.updated_at) if review.updated_at else None,
                }

            return {
                "date": str(review_date),
                "a_share_closed": indices_data["a_share_closed"],
                "hk_closed": indices_data["hk_closed"],
                "us_closed": indices_data["us_closed"],
                "indices": indices,
                "daily_pnl": round(daily_pnl, 2),
                "daily_pnl_pct": daily_pnl_pct,
                "positions": positions,
                "positions_count": len(positions),
                "trades": day_trades,
                "trades_count": len(day_trades),
                "signals": day_signals,
                "signals_total": signals_total,
                "signals_count": len(day_signals),
                "decisions": decisions,
                "decisions_count": len(decisions),
                "decisions_filled": decisions_filled,
                "decisions_rejected": decisions_rejected,
                "decisions_expired": decisions_expired,
                "decisions_failed": decisions_failed,
                "review": review_data,
                "template": BEGINNER_TEMPLATE,
            }

        finally:
            session.close()

    def save_note(self, review_date: date, note: str, self_score: Optional[int] = None,
                  template_used: str = "beginner") -> Dict[str, Any]:
        """保存复盘笔记和自评分"""
        session = get_session()
        try:
            review = session.query(DailyReview).filter(
                DailyReview.review_date == review_date
            ).first()

            if not review:
                # 获取当日快照数据
                day_trades = session.query(ManualTrade).filter(
                    ManualTrade.trade_date == review_date
                ).all()
                day_signals = session.query(Signal).filter(
                    Signal.date == review_date
                ).all()

                # 正确计算当日盈亏
                positions_daily = self._get_positions_daily(session, review_date)
                daily_pnl = sum(p.get("daily_pnl", 0) or 0 for p in positions_daily)

                # 获取指数快照
                indices = self._fetch_indices_from_history(review_date)
                if not indices and review_date == market_today(A_SHARE):
                    indices = self._fetch_indices_realtime()
                index_snapshot_str = None
                if indices and any(r.get("price") is not None for r in indices):
                    index_snapshot_str = json.dumps(indices, ensure_ascii=False)

                review = DailyReview(
                    review_date=review_date,
                    daily_pnl=round(daily_pnl, 2),
                    positions_count=len(positions_daily),
                    trades_count=len(day_trades),
                    signals_count=len(day_signals),
                    index_snapshot=index_snapshot_str,
                    template_used=template_used,
                )
                session.add(review)

            review.note = note
            review.template_used = template_used
            if self_score is not None:
                review.self_score = max(1, min(10, self_score))
                # 重算综合分
                if review.ai_score is not None:
                    review.composite_score = round((review.self_score + review.ai_score) / 2, 1)
                else:
                    review.composite_score = float(review.self_score)

            session.commit()
            session.refresh(review)

            return {
                "id": review.id,
                "review_date": str(review.review_date),
                "self_score": review.self_score,
                "ai_score": review.ai_score,
                "composite_score": review.composite_score,
                "note": review.note,
                "updated_at": str(review.updated_at) if review.updated_at else None,
            }

        except Exception as e:
            session.rollback()
            logger.error(f"保存复盘笔记失败: {e}")
            raise
        finally:
            session.close()

    def request_ai_score(self, review_date: date) -> Dict[str, Any]:
        """
        请求 AI 评分（非流式，直接返回结果）
        """
        session = get_session()
        try:
            # 收集当日数据
            data = self.get_review_data(review_date)
            review = session.query(DailyReview).filter(
                DailyReview.review_date == review_date
            ).first()

            if not review:
                review = DailyReview(
                    review_date=review_date,
                    positions_count=data["positions_count"],
                    trades_count=data["trades_count"],
                    signals_count=data["signals_count"],
                )
                session.add(review)
                session.commit()

            # 构建 prompt
            system_prompt = self._build_ai_score_system_prompt()
            user_prompt = self._build_ai_score_user_prompt(data)

            # 调用 LLM
            api_key = os.getenv("OPENAI_API_KEY", "")
            base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

            if not api_key:
                raise ValueError("OPENAI_API_KEY 未配置")

            from llm_client import build_client
            client = build_client(base_url=base_url, api_key=api_key)
            logger.info(f"AI评分请求 | date={review_date}")

            response = client.chat.completions.create(
                **normalize_chat_params(dict(
                    model=get_cheap_model(),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=4096,  # GPT-5 推理 token 也计入此额度，留足空间
                    temperature=0.5,
                ))
            )

            content = response.choices[0].message.content or ""

            # 解析 AI 返回的评分
            score, reason, structured = self._parse_ai_score_response(content)

            # 更新记录
            review.ai_score = score
            review.ai_score_reason = reason
            if structured:
                review.ai_dimension_scores = json.dumps(structured, ensure_ascii=False)
            if review.self_score is not None:
                review.composite_score = round((review.self_score + score) / 2, 1)
            else:
                review.composite_score = float(score)

            session.commit()
            session.refresh(review)

            # 解析存储的 JSON
            dim_scores = None
            if review.ai_dimension_scores:
                try:
                    dim_scores = json.loads(review.ai_dimension_scores)
                except json.JSONDecodeError:
                    pass

            return {
                "ai_score": review.ai_score,
                "ai_score_reason": review.ai_score_reason,
                "ai_dimension_scores": dim_scores,
                "composite_score": review.composite_score,
                "self_score": review.self_score,
            }

        except Exception as e:
            session.rollback()
            logger.error(f"AI评分请求失败: {e}")
            raise
        finally:
            session.close()

    def get_calendar(self, year: int, month: int) -> List[Dict[str, Any]]:
        """获取指定月份有复盘记录的日期列表"""
        session = get_session()
        try:
            from sqlalchemy import extract
            reviews = session.query(DailyReview).filter(
                extract('year', DailyReview.review_date) == year,
                extract('month', DailyReview.review_date) == month,
            ).all()

            return [
                {
                    "date": str(r.review_date),
                    "composite_score": r.composite_score,
                    "self_score": r.self_score,
                    "ai_score": r.ai_score,
                    "has_note": bool(r.note),
                }
                for r in reviews
            ]
        finally:
            session.close()

    def get_summary(self, limit: int = 30) -> List[Dict[str, Any]]:
        """获取最近 N 天复盘摘要"""
        session = get_session()
        try:
            reviews = (
                session.query(DailyReview)
                .order_by(DailyReview.review_date.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "date": str(r.review_date),
                    "daily_pnl": r.daily_pnl,
                    "composite_score": r.composite_score,
                    "self_score": r.self_score,
                    "ai_score": r.ai_score,
                    "trades_count": r.trades_count,
                    "has_note": bool(r.note),
                }
                for r in reviews
            ]
        finally:
            session.close()

    # ==================== 内部方法 ====================

    # 固定顺序和名称
    INDEX_ORDER = ["000001", "399001", "399006", "HSI", "SPX"]
    INDEX_NAMES = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
        "HSI": "恒生指数",
        "SPX": "标普500",
    }
    # 东方财富历史 K 线 secid
    INDEX_SECIDS = {
        "000001": "1.000001",
        "399001": "0.399001",
        "399006": "0.399006",
        "HSI": "100.HSI",
        "SPX": "100.SPX",
    }

    # 市场分组: 哪些指数属于哪个市场
    MARKET_GROUPS = {
        "a_share": ["000001", "399001", "399006"],
        "hk": ["HSI"],
        "us": ["SPX"],
    }

    def _get_indices(self, session, review_date: date) -> Dict[str, Any]:
        """
        获取大盘指数表现（按 review_date）

        返回:
        {
            "a_share_closed": bool,
            "hk_closed": bool,
            "us_closed": bool,
            "items": [...]
        }
        """
        # 1) 尝试读取已有快照
        review = session.query(DailyReview).filter(
            DailyReview.review_date == review_date
        ).first()
        if review and review.index_snapshot:
            try:
                cached = json.loads(review.index_snapshot)
                if isinstance(cached, dict) and "a_share_closed" in cached:
                    return cached
                # 兼容旧格式 → 作废，重新拉取
            except json.JSONDecodeError:
                pass

        # 2) 周末：全部休市
        if review_date.weekday() >= 5:
            result = self._build_indices_result(self._empty_indices(self.INDEX_NAMES), all_closed=True)
            self._save_index_snapshot(session, review, review_date, result)
            return result

        # 3) 从东方财富历史 K 线获取
        items = self._fetch_indices_from_history(review_date)

        # 4) 当天且历史无数据 → 降级到实时
        if not items and review_date == market_today(A_SHARE):
            items = self._fetch_indices_realtime()

        if items:
            result = self._build_indices_result(items)
        else:
            result = self._build_indices_result(self._empty_indices(self.INDEX_NAMES), all_closed=True)

        self._save_index_snapshot(session, review, review_date, result)
        return result

    def _build_indices_result(self, items: List[Dict[str, Any]], all_closed: bool = False) -> Dict[str, Any]:
        """根据 items 中各指数是否有数据，判断各市场休市状态"""
        if all_closed:
            return {"a_share_closed": True, "hk_closed": True, "us_closed": True, "items": items}

        prices_by_symbol = {it["symbol"]: it.get("price") for it in items}
        closed = {}
        for market, codes in self.MARKET_GROUPS.items():
            closed[f"{market}_closed"] = all(prices_by_symbol.get(c) is None for c in codes)

        return {**closed, "items": items}

    def _save_index_snapshot(self, session, review, review_date: date, result: Dict[str, Any]):
        """把指数数据快照到 DailyReview，没有记录则创建"""
        snapshot_json = json.dumps(result, ensure_ascii=False)
        try:
            if review:
                if not review.index_snapshot:
                    review.index_snapshot = snapshot_json
                    session.commit()
            else:
                review = DailyReview(review_date=review_date, index_snapshot=snapshot_json)
                session.add(review)
                session.commit()
        except Exception:
            session.rollback()

    def _fetch_one_index_kline(self, sess, code: str, date_str: str) -> Dict[str, Any]:
        """拉单个指数的历史 K 线（供并发调用）"""
        secid = self.INDEX_SECIDS[code]
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": 101,
            "fqt": 1,
            "beg": date_str,
            "end": date_str,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "_": str(int(time.time() * 1000)),
        }
        resp = sess.get(url, params=params, timeout=8)
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        if klines:
            fields = klines[0].split(",")
            return {
                "symbol": code,
                "name": self.INDEX_NAMES[code],
                "price": float(fields[2]),
                "change_pct": float(fields[8]) if len(fields) > 8 else None,
            }
        return {"symbol": code, "name": self.INDEX_NAMES[code], "price": None, "change_pct": None}

    def _fetch_indices_from_history(self, review_date: date, max_retries: int = 3) -> List[Dict[str, Any]]:
        """从东财历史 K 线并发获取指定日期的 5 个指数。

        5 个 K 线请求**共享一个快代理 IP**（`domestic_rotate`）：任一请求失败就整批换 IP
        重来，先直连失败才换代理（铁律）。此前手写代理循环，取不到 IP 时 proxies=None
        静默直连——已消除。
        """
        from net import ProxyExhaustedError, domestic_rotate, make_domestic_session
        date_str = review_date.strftime("%Y%m%d")

        def _batch(proxies: Optional[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
            sess = make_domestic_session(proxies)
            try:
                results_map: Dict[str, Dict[str, Any]] = {}
                with ThreadPoolExecutor(max_workers=5) as pool:
                    futures = {
                        pool.submit(self._fetch_one_index_kline, sess, code, date_str): code
                        for code in self.INDEX_ORDER
                    }
                    for fut in as_completed(futures):
                        # 任一请求抛异常 → 整批失败 → domestic_rotate 换 IP 重来
                        results_map[futures[fut]] = fut.result()
                return results_map
            finally:
                sess.close()

        try:
            results_map = domestic_rotate(_batch, what="复盘指数历史K线",
                                          prefer_direct=True, max_rounds=max_retries)
        except ProxyExhaustedError:
            return []

        result = [results_map.get(c, {"symbol": c, "name": self.INDEX_NAMES[c],
                                      "price": None, "change_pct": None})
                  for c in self.INDEX_ORDER]
        if any(r["price"] is not None for r in result):
            return result
        return []  # 全部成功但无数据 → 休市

    def _fetch_indices_realtime(self, max_retries: int = 3) -> List[Dict[str, Any]]:
        """从东财实时快照获取当前指数（历史 K 线取不到时的降级方案）。

        取数收口在 `quote_router.fetch_index_snapshot`（先直连失败才换快代理，铁律）。
        `max_retries` 保留仅为签名兼容，换 IP 轮次现由收口内部管。
        """
        del max_retries
        from acquisition.markets.quote_router import fetch_index_snapshot
        rows = fetch_index_snapshot([self.INDEX_SECIDS[c] for c in self.INDEX_ORDER])
        if not rows:
            return []
        by_code = {r["code"]: r for r in rows}
        return [{
            "symbol": code,
            "name": self.INDEX_NAMES[code],
            "price": by_code[code]["price"] if code in by_code else None,
            "change_pct": by_code[code]["change_pct"] if code in by_code else None,
        } for code in self.INDEX_ORDER]

    @staticmethod
    def _empty_indices(name_map: Dict[str, str]) -> List[Dict[str, Any]]:
        return [{"symbol": code, "name": name, "price": None, "change_pct": None}
                for code, name in name_map.items()]

    def _get_positions_daily(self, session, review_date: date) -> List[Dict[str, Any]]:
        """
        获取持仓在当日的表现

        日盈亏 = 昨持有还在的部分 + 当日卖出部分 + 当日新买还在的部分
        """
        calculator = PortfolioCalculator()
        # 日初持仓（截止前一天）
        pos_yesterday = {p["symbol"]: p for p in calculator.get_current_positions(as_of_date=review_date - __import__('datetime').timedelta(days=1))}
        # 日终持仓（截止今天）
        pos_today = {p["symbol"]: p for p in calculator.get_current_positions(as_of_date=review_date)}

        # 当日交易按 symbol 汇总
        day_trades = session.query(ManualTrade).filter(ManualTrade.trade_date == review_date).all()
        day_buys: Dict[str, int] = {}   # symbol → 当日买入总量
        day_sells: Dict[str, List] = {}  # symbol → [(qty, price), ...]
        for t in day_trades:
            if t.side == "BUY":
                day_buys[t.symbol] = day_buys.get(t.symbol, 0) + t.quantity
            elif t.side == "SELL":
                day_sells.setdefault(t.symbol, []).append((t.quantity, t.price))

        # 涉及的所有 symbol（日初有 or 日终有 or 当日交易过）
        all_syms = set(pos_yesterday) | set(pos_today) | {t.symbol for t in day_trades}

        result = []
        for sym in all_syms:
            yesterday = pos_yesterday.get(sym)
            today = pos_today.get(sym)
            qty_yesterday = yesterday["quantity"] if yesterday else 0
            qty_today = today["quantity"] if today else 0

            # 当日收盘价
            today_quote = session.query(DailyQuote).filter(
                DailyQuote.symbol == sym, DailyQuote.date == review_date,
            ).first()
            today_close = today_quote.close if today_quote else None

            # 前一日收盘价
            prev_quote = session.query(DailyQuote).filter(
                DailyQuote.symbol == sym, DailyQuote.date < review_date,
            ).order_by(DailyQuote.date.desc()).first()
            prev_close = prev_quote.close if prev_quote else None

            daily_pnl = 0.0
            has_data = False

            # 1) 卖出部分：(sell_price - prev_close) × qty
            for sell_qty, sell_price in day_sells.get(sym, []):
                if prev_close is not None:
                    daily_pnl += (sell_price - prev_close) * sell_qty
                    has_data = True
                elif yesterday is None and day_buys.get(sym, 0) > 0:
                    # T+0: 当日买入又卖出，用买入均价做基准
                    buy_trades_t0 = [t for t in day_trades if t.symbol == sym and t.side == "BUY"]
                    total_cost_t0 = sum(t.price * t.quantity for t in buy_trades_t0)
                    total_qty_t0 = sum(t.quantity for t in buy_trades_t0)
                    avg_buy_t0 = total_cost_t0 / total_qty_t0 if total_qty_t0 > 0 else sell_price
                    daily_pnl += (sell_price - avg_buy_t0) * sell_qty
                    has_data = True

            # 2) 昨天持有、今天还在的部分：(close - prev_close) × qty
            bought_today = day_buys.get(sym, 0)
            total_sold = sum(qty for qty, _ in day_sells.get(sym, []))
            held_from_yesterday = max(0, qty_yesterday - total_sold)
            new_buy_remaining = qty_today - held_from_yesterday

            if held_from_yesterday > 0 and today_close is not None and prev_close is not None:
                daily_pnl += (today_close - prev_close) * held_from_yesterday
                has_data = True

            # 3) 当日新买且还在的部分：(close - avg_buy_price) × qty
            if new_buy_remaining > 0 and today_close is not None and bought_today > 0:
                # 当日买入均价（从交易记录算）
                buy_trades = [t for t in day_trades if t.symbol == sym and t.side == "BUY"]
                total_cost = sum(t.price * t.quantity for t in buy_trades)
                total_qty = sum(t.quantity for t in buy_trades)
                avg_buy = total_cost / total_qty if total_qty > 0 else 0
                daily_pnl += (today_close - avg_buy) * new_buy_remaining
                has_data = True

            daily_pnl = round(daily_pnl, 2) if has_data else None

            # 日盈亏百分比
            # 分母 = 昨日持有市值(prev_close × held_from_yesterday) + 当日新买成本(avg_buy × new_buy_remaining)
            daily_pnl_pct = None
            if daily_pnl is not None:
                base_value = 0.0
                if held_from_yesterday > 0 and prev_close is not None:
                    base_value += prev_close * held_from_yesterday
                # 卖出部分也要算进基底（基于 prev_close 或买入均价）
                for sell_qty, _ in day_sells.get(sym, []):
                    if prev_close is not None and qty_yesterday > 0:
                        base_value += prev_close * sell_qty
                    elif yesterday is None and day_buys.get(sym, 0) > 0:
                        buy_trades_base = [t for t in day_trades if t.symbol == sym and t.side == "BUY"]
                        tc = sum(t.price * t.quantity for t in buy_trades_base)
                        tq = sum(t.quantity for t in buy_trades_base)
                        base_value += (tc / tq if tq > 0 else 0) * sell_qty
                if new_buy_remaining > 0 and bought_today > 0:
                    buy_trades_nb = [t for t in day_trades if t.symbol == sym and t.side == "BUY"]
                    tc_nb = sum(t.price * t.quantity for t in buy_trades_nb)
                    tq_nb = sum(t.quantity for t in buy_trades_nb)
                    avg_buy_nb = tc_nb / tq_nb if tq_nb > 0 else 0
                    base_value += avg_buy_nb * new_buy_remaining
                if base_value > 0:
                    daily_pnl_pct = round(daily_pnl / base_value * 100, 2)

            # 涨跌幅（基于 prev_close 或买入均价）
            daily_change_pct = None
            base_price = prev_close if qty_yesterday > 0 else (today.get("avg_cost") if today else None)
            if today_close is not None and base_price is not None and base_price > 0:
                daily_change_pct = round((today_close - base_price) / base_price * 100, 2)

            # 只返回日终仍有持仓的，但 daily_pnl 包含了卖出部分
            if today:
                result.append({
                    **today,
                    "today_close": today_close,
                    "prev_close": prev_close if qty_yesterday > 0 else today.get("avg_cost"),
                    "daily_change_pct": daily_change_pct,
                    "daily_pnl": daily_pnl,
                    "daily_pnl_pct": daily_pnl_pct,
                })
            elif has_data:
                # 当日全部卖出 — 不在持仓列表里，但盈亏要计入汇总
                name = yesterday["name"] if yesterday else sym
                # 从日终持仓算 realized_pnl；如果没有则用昨日的加上今日卖出盈亏
                if yesterday:
                    realized = yesterday.get("realized_pnl", 0)
                    # 把今天卖出的盈亏也加到 realized
                    for sq, sp in day_sells.get(sym, []):
                        realized += (sp - yesterday["avg_cost"]) * sq
                else:
                    realized = 0
                result.append({
                    "symbol": sym,
                    "name": name,
                    "quantity": 0,
                    "avg_cost": yesterday["avg_cost"] if yesterday else 0,
                    "current_price": None,
                    "market_value": 0,
                    "total_cost": 0,
                    "unrealized_pnl": 0,
                    "unrealized_pnl_pct": 0,
                    "realized_pnl": round(realized, 2),
                    "today_close": today_close,
                    "prev_close": prev_close,
                    "daily_change_pct": None,
                    "daily_pnl": daily_pnl,
                    "daily_pnl_pct": daily_pnl_pct,
                })

        return result

    def _get_day_trades(self, session, review_date: date) -> List[Dict[str, Any]]:
        """获取当日交易"""
        trades = session.query(ManualTrade).filter(
            ManualTrade.trade_date == review_date,
        ).order_by(ManualTrade.id.asc()).all()

        return [
            {
                "id": t.id,
                "symbol": t.symbol,
                "name": t.name,
                "side": t.side,
                "price": t.price,
                "quantity": t.quantity,
                "amount": t.amount,
                "commission": t.commission,
                "note": t.note,
                "ai_recommended_price": t.ai_recommended_price,
                "source_type": t.source_type,
                "pending_order_id": t.pending_order_id,
            }
            for t in trades
        ]

    def _get_day_decisions(self, session, review_date: date) -> List[Dict[str, Any]]:
        """获取当日所有已决策的 PendingOrder（status != PENDING）"""
        # ⛔ `PendingOrder.created_at` 是 `server_default=func.now()` 写的，SQLite 落 **UTC**，
        # 而原来的边界是**本地**零点/午夜 —— 两者差 8 小时，复盘按日归集会把北京
        # 00:00–08:00 的决策算到前一天、把 16:00–24:00 的漏掉。
        day_start, day_end = market_day_bounds(A_SHARE, review_date, storage="utc")
        orders = (
            session.query(PendingOrder)
            .filter(
                PendingOrder.created_at >= day_start,
                PendingOrder.created_at < day_end,
                PendingOrder.status != "PENDING",
            )
            .order_by(PendingOrder.created_at.asc())
            .all()
        )

        result = []
        for o in orders:
            # 解析 JSON 字段
            reasons = []
            if o.reasons:
                try:
                    reasons = json.loads(o.reasons)
                except (json.JSONDecodeError, TypeError):
                    pass
            risk_detail = []
            if o.risk_check_detail:
                try:
                    risk_detail = json.loads(o.risk_check_detail)
                except (json.JSONDecodeError, TypeError):
                    pass

            result.append({
                "order_id": o.order_id,
                "symbol": o.symbol,
                "name": o.name,
                "signal_type": o.signal_type,
                "strategy": o.strategy,
                "strength": o.strength,
                "suggested_price": o.suggested_price,
                "stop_loss": o.stop_loss,
                "take_profit": o.take_profit,
                "reasons": reasons,
                "status": o.status,
                "actual_price": o.actual_price,
                "reject_reason": o.reject_reason,
                "risk_check_detail": risk_detail,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
            })

        return result

    def _get_day_signals(self, session, review_date: date,
                         limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
        """获取当日触发的信号（分页），并标注用户是否持有"""
        signals = (
            session.query(Signal)
            .filter(Signal.date == review_date)
            .order_by(Signal.strength.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # 获取截止该日期的持仓 symbol 集合
        calculator = PortfolioCalculator()
        positions = calculator.get_current_positions(as_of_date=review_date)
        held_symbols = {p["symbol"] for p in positions}

        # 获取当日卖出的 symbol
        sold_today = session.query(ManualTrade.symbol).filter(
            ManualTrade.trade_date == review_date,
            ManualTrade.side == "SELL",
        ).all()
        sold_symbols = {s[0] for s in sold_today}

        result = []
        for s in signals:
            # 获取股票名
            stock = session.query(StockInfo).filter(StockInfo.symbol == s.symbol).first()
            name = stock.name if stock else s.symbol

            holding_status = "not_held"
            if s.symbol in held_symbols:
                holding_status = "held"
            elif s.symbol in sold_symbols:
                holding_status = "sold_today"

            result.append({
                "id": s.id,
                "symbol": s.symbol,
                "name": name,
                "signal_type": s.signal_type,
                "strength": s.strength,
                "price": s.price,
                "strategy": s.strategy,
                "holding_status": holding_status,
            })

        return result

    def _build_ai_score_system_prompt(self) -> str:
        return """你是一位资深投资教练，专注于帮助投资新手建立正确的交易习惯和纪律。

你的任务是根据用户当天的交易数据和复盘笔记，从五个维度分别打分，并给出综合评分和详细评语。

评分维度和权重：
1. 纪律执行（30%）：是否按信号操作，是否设了止损，有没有冲动交易
2. 仓位管理（20%）：单股仓位是否合理，整体仓位是否安全
3. 买卖时机（20%）：成交价是否合理，有没有追高杀跌
4. 信号跟进（15%）：强信号是否跟进了，弱信号是否忍住了
5. 自我反思（15%）：复盘笔记质量，有没有认真总结

评分标准：
- 1-3分：犯了严重错误（重仓追高、无止损、完全忽视信号）
- 4-5分：有失误但不致命
- 6-7分：中规中矩，基本按计划执行
- 8-9分：表现优秀，操作有纪律
- 10分：完美执行

你的回复必须严格按照以下 JSON 格式，不要包含其他内容：
```json
{
  "score": <1-10的整数，综合评分>,
  "dimensions": {
    "discipline":    { "score": <1-10>, "comment": "<一句话评价纪律执行>" },
    "position":      { "score": <1-10>, "comment": "<一句话评价仓位管理>" },
    "timing":        { "score": <1-10>, "comment": "<一句话评价买卖时机>" },
    "signal_follow": { "score": <1-10>, "comment": "<一句话评价信号跟进>" },
    "reflection":    { "score": <1-10>, "comment": "<一句话评价自我反思>" }
  },
  "highlights": ["<做得好的亮点1>", "<亮点2>"],
  "improvements": ["<需要改进的点1>", "<改进点2>"],
  "reason": "<Markdown格式的详细评语，包含整体分析、具体建议>"
}
```

要求：
- dimensions 中每个维度必须有 score(1-10整数) 和 comment(简短一句话)
- highlights 和 improvements 各 2-4 条，简洁有力
- reason 是完整的 Markdown 评语，可以包含小标题、列表等"""

    def _build_ai_score_user_prompt(self, data: Dict[str, Any]) -> str:
        parts = [f"## 复盘日期: {data['date']}\n"]

        # 大盘
        parts.append("### 当日大盘")
        for idx in data.get("indices", []):
            if idx["price"] is not None:
                pct = f"{idx['change_pct']:+.2f}%" if idx["change_pct"] is not None else "N/A"
                parts.append(f"- {idx['name']}: {idx['price']} ({pct})")

        # 持仓
        parts.append(f"\n### 当前持仓 ({data['positions_count']} 只)")
        for p in data.get("positions", []):
            daily = f"日涨跌 {p['daily_change_pct']:+.2f}%" if p.get("daily_change_pct") is not None else ""
            pnl = f"日盈亏 {p['daily_pnl']:+.0f}" if p.get("daily_pnl") is not None else ""
            parts.append(f"- {p['name']}({p['symbol']}): 均价{p['avg_cost']:.2f} 持{p['quantity']}股 {daily} {pnl}")

        # 当日交易
        parts.append(f"\n### 当日交易 ({data['trades_count']} 笔)")
        if data.get("trades"):
            for t in data["trades"]:
                ai_price = f" (AI推荐价: {t['ai_recommended_price']})" if t.get("ai_recommended_price") else ""
                parts.append(f"- {t['side']} {t['name']}({t['symbol']}) {t['price']}x{t['quantity']} 金额{t['amount']:.0f}{ai_price}")
        else:
            parts.append("- 今日无交易")

        # 当日信号
        parts.append(f"\n### 当日信号 ({data['signals_count']} 个)")
        for s in data.get("signals", []):
            status_map = {"held": "已持有", "sold_today": "今日已卖", "not_held": "未持有"}
            status = status_map.get(s["holding_status"], "")
            parts.append(f"- {s['name']} {s['strategy']} {s['signal_type']} 强度{s['strength']:.2f} [{status}]")

        # 自动化决策回顾
        decisions = data.get("decisions", [])
        if decisions:
            parts.append(f"\n### 自动化决策回顾 ({data.get('decisions_count', 0)} 个)")
            parts.append(f"- 执行: {data.get('decisions_filled', 0)} | 拒绝: {data.get('decisions_rejected', 0)} | 过期: {data.get('decisions_expired', 0)} | 失败: {data.get('decisions_failed', 0)}")
            for d in decisions:
                reasons_str = ", ".join(r.get("detail", r.get("indicator", "")) for r in d.get("reasons", [])[:3])
                if d["status"] == "FILLED":
                    parts.append(f"- ✅ {d['name']}({d['symbol']}) {d['signal_type']} 策略={d['strategy']} 强度={d['strength']:.2f} 成交价={d.get('actual_price', 'N/A')} | {reasons_str}")
                elif d["status"] == "REJECTED":
                    parts.append(f"- ❌ {d['name']}({d['symbol']}) {d['signal_type']} 策略={d['strategy']} 强度={d['strength']:.2f} 被拒绝: {d.get('reject_reason', '')} | {reasons_str}")
                elif d["status"] == "EXPIRED":
                    parts.append(f"- ⏰ {d['name']}({d['symbol']}) {d['signal_type']} 策略={d['strategy']} 强度={d['strength']:.2f} 过期未处理 | {reasons_str}")
                elif d["status"] == "FAILED":
                    parts.append(f"- ⚠️ {d['name']}({d['symbol']}) {d['signal_type']} 策略={d['strategy']} 强度={d['strength']:.2f} 执行失败: {d.get('reject_reason', '')} | {reasons_str}")

        # 复盘笔记
        review = data.get("review")
        if review and review.get("note"):
            parts.append(f"\n### 用户复盘笔记\n{review['note']}")
        else:
            parts.append("\n### 用户复盘笔记\n（用户未填写复盘笔记）")

        if review and review.get("self_score"):
            parts.append(f"\n用户自评分: {review['self_score']}/10")

        return "\n".join(parts)

    def _parse_ai_score_response(self, content: str) -> tuple[int, str, dict | None]:
        """
        解析 AI 返回的评分 JSON

        返回: (score, reason, structured_data)
        structured_data 包含 dimensions / highlights / improvements（可能为 None）
        """
        try:
            # 可能被 ```json ``` 包裹
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
            else:
                json_str = content.strip()

            parsed = json.loads(json_str)
            score = max(1, min(10, int(parsed.get("score", 5))))
            reason = parsed.get("reason", content)

            # 提取结构化数据
            structured = None
            dimensions = parsed.get("dimensions")
            if dimensions and isinstance(dimensions, dict):
                structured = {
                    "dimensions": {},
                    "highlights": parsed.get("highlights", []),
                    "improvements": parsed.get("improvements", []),
                }
                for key in ("discipline", "position", "timing", "signal_follow", "reflection"):
                    dim = dimensions.get(key, {})
                    if isinstance(dim, dict):
                        structured["dimensions"][key] = {
                            "score": max(1, min(10, int(dim.get("score", 5)))),
                            "comment": str(dim.get("comment", "")),
                        }

            return score, reason, structured
        except (json.JSONDecodeError, IndexError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning(f"AI评分解析失败，使用原始内容: {e}")
            return 5, content, None

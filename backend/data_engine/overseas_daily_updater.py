"""港美股日线**增量**更新 —— 每日链的海外侧。

## 为什么要有这个文件（2026-07-17）

`DailyUpdater` 硬过滤 `market == "a_share"`，港美股**从来就不在每日链范围内**。
它们唯一的通道是深历史回补 `deep_history/overseas_job.py`，而那个 job
**结构上不可能做增量** —— `_resolve_universe` 的逻辑是「只要这只票在 daily_quotes
里有任意一行，就永远跳过」。所以它只能把票从「零数据」拉到「有数据」，一旦有了
就再也不碰。实测后果：2026-07-17 时港股行情停在 07-08、美股停在 07-06，
**那是深历史最后一次跑完的日子，之后再也没动过**，而且不会自己变新。

代价被 P0-1 后验评估器量化出来了：`report_picks` 的 10 条推荐里 **6 条是港美股，
永远评不了** —— 六成的港美股推荐等于没给过。

## 为什么不复用 DailyUpdater 加个 market 参数

`DailyUpdater._update_stream_impl` 是 700 行单函数，其中对港美股 **100% 无用**的有：
东财代理池慢路径（`ProxyPool` + `EastMoneyCrawler` + secid_market + 熔断，约 250 行）、
`_probe_latest_trading_date` 打东财、`_is_market_closed` 写死 15:05 北京时间、
pytdx 指数路由。真正能复用的只有「分类 + 写库 + 事件」三段。塞 `if market` 会让它
裂成两条几乎不相交的执行路径 —— 改动面更大且更脏。

## 与 A 股链的关系：**独立 job、独立 cron 16:30**（2026-07-17 Jason 拍板）

两个理由，一石二鸟：
1. **港股 16:00 HKT 才收盘**，主链 15:35 跑，那会儿拉港股拿到的是没收盘的半截子。
   （美股无此问题：美东 16:00 收盘 = 北京凌晨 4-5 点，15:35 拿到的是 11h 前已收的
   完整日线。）
2. 港美股 ~16k 只要跑 10-15 分钟，**串在主链里会把后面的信号回补/追踪/涨停预测/
   估值刷新全部推迟**。

## 出网合规

**不许 `import yfinance`** —— `tests/net/test_egress_single_entry.py` 在 AST 层面
检测 import，只有 `acquisition`/`net` 顶包豁免。一律走 `acquisition.markets.yf_batch`
的门面（代理已由 `configure_yf_proxy()` 按 net.overseas 策略注入）。

⚠️ 代理铁律（`docs/CODING_STANDARDS.md` §8.2「国内抓取失败只换代理 IP，禁止降级
直连」）**管的是国内通道，不管这里** —— 海外走 `resolve_overseas_proxy()`，
返回 None 就是合法直连。
"""
import os
import random
import time
from datetime import date, datetime, timedelta

from loguru import logger

from common.market import to_yf_symbol
from data_engine.deep_history.bulk_upsert import bulk_upsert_quotes
from data_engine.storage.database import get_session
from data_engine.storage.models import DailyQuote, StockInfo

MARKETS = ("hk_stock", "us_stock")

# 只抓这两类。`excluded_bond_note` / `excluded_rmb_counter`（港股）/
# `excluded_leveraged_etf`（美股）不抓 —— 口径与 overseas_job.py:163 一致。
# ⚠️ 注意港美股是靠 **stock_type** 排除的，`is_active` 全是 1（美股 914 只
# excluded_* 也是 is_active=1），所以过滤条件不能只看 is_active。
_TRADABLE_TYPES = ("stock", "etf")

# 每批喂给 yf.download 的 symbol 数。比深历史的 50 大 —— 增量只拉最近几天，
# 单批数据量小得多。
BATCH_SIZE = int(os.getenv("OVERSEAS_DAILY_BATCH_SIZE", "100"))

# 批间静默。节奏照抄 overseas_job（那边是跑了很久验出来的经验值），带随机抖动。
SLEEP_BETWEEN_BATCHES = float(os.getenv("OVERSEAS_DAILY_SLEEP", "3.0"))

# 单批超时。yfinance 没有原生超时参数，靠 _call_with_timeout 线程+join 兜底。
BATCH_FETCH_TIMEOUT = float(os.getenv("OVERSEAS_DAILY_TIMEOUT", "180"))

# 最多往回拉多少天。增量只管「最近断的这几天」；缺得比这还多的票是**深历史的活**，
# 不该让每日增量去拉十年数据把一次运行拖死。
MAX_LOOKBACK_DAYS = int(os.getenv("OVERSEAS_DAILY_MAX_LOOKBACK", "90"))

# 连通性预检锚点：一定有数据的票。复用 overseas_job:258 的选择。
_ANCHOR = {"hk_stock": "00700.HK", "us_stock": "AAPL"}


def _to_yf(symbol: str, market: str) -> str:
    """库里的 symbol → yfinance 认的 symbol。

    港股库里是 **5 位**（`09988.HK`），yfinance 要 **4 位**（`0988.HK`）——
    必须过 `common.market.to_yf_symbol`。美股是裸 ticker，原样传。
    """
    return to_yf_symbol(symbol) if market == "hk_stock" else symbol


class OverseasDailyUpdater:
    """港美股日线增量更新。一次 `run(market)` 处理一个市场。"""

    def _universe(self, session, market: str) -> list[StockInfo]:
        return session.query(StockInfo).filter(
            StockInfo.market == market,
            StockInfo.is_active == 1,
            StockInfo.stock_type.in_(_TRADABLE_TYPES),
        ).all()

    @staticmethod
    def _latest_dates(session, market: str) -> dict[str, date]:
        """每只票在库里的最新日线日期。一次聚合查询，不 N+1。"""
        from sqlalchemy import func
        rows = session.query(
            DailyQuote.symbol, func.max(DailyQuote.date),
        ).filter(DailyQuote.market == market).group_by(DailyQuote.symbol).all()
        out: dict[str, date] = {}
        for sym, d in rows:
            if isinstance(d, str):
                try:
                    d = datetime.strptime(d, "%Y-%m-%d").date()
                except ValueError:
                    continue
            elif isinstance(d, datetime):
                d = d.date()
            if d:
                out[sym] = d
        return out

    @staticmethod
    def _start_for(latest: date, today: date) -> str:
        """某只票该从哪天开始拉 = 它最新那根的次日，最多回看 MAX_LOOKBACK_DAYS。

        钳住是因为：缺得比这还多的票是**深历史的活**，不该让每日增量去拉十年数据
        把一次运行拖死。
        """
        start = max(latest + timedelta(days=1), today - timedelta(days=MAX_LOOKBACK_DAYS))
        return start.isoformat()

    def _plan(self, session, market: str, today: date) -> tuple[list[str], dict[str, date], dict]:
        """挑出该更新的票 + 每只票在库里的最新日期。

        **todo 按落后程度排序**（最新的在前），这样 `run()` 分批时同一批里的票
        落后天数相近，每批能各算各的起点 —— 否则一只落后一年的票会把**所有**票的
        起点都拖到 90 天前，16k 只全拉 90 根 bar 而不是 9 根，白烧 10 倍流量。

        Returns:
            `(todo, latest, stats)` —— `latest` 显式返回给 `run()` 算每批起点，
            不走实例属性（隐式跨方法状态谁设谁用全靠约定，容易埋雷）。
            `stats["start"]` 是**全局最早**起点，仅供日志/统计。
        """
        universe = self._universe(session, market)
        latest = self._latest_dates(session, market)

        todo: list[str] = []
        skipped_fresh = 0
        skipped_no_data = 0
        for s in universe:
            d = latest.get(s.symbol)
            if d is None:
                # 一根 bar 都没有 = **深历史的活**，不是增量的。让增量去拉十年数据
                # 会把一次运行拖死，而且深历史有断点续跑/confirmed_no_data 那套。
                skipped_no_data += 1
                continue
            if d >= today:
                skipped_fresh += 1
                continue
            todo.append(s.symbol)

        stats = {
            "universe": len(universe),
            "todo": len(todo),
            "skipped_fresh": skipped_fresh,
            "skipped_no_data": skipped_no_data,
        }
        if not todo:
            return [], {}, stats

        todo.sort(key=lambda s: latest[s], reverse=True)   # 最新的在前，同批落后程度相近
        oldest = min(latest[s] for s in todo)
        stats["start"] = self._start_for(oldest, today)
        stats["oldest_latest"] = oldest.isoformat()
        return todo, latest, stats

    def _fetch_batch(self, market: str, batch: list[str], start: str) -> dict:
        """一批 symbol → {原始 symbol: DataFrame or None}。形状照抄 overseas_job._fetch_batch。"""
        from acquisition.markets.yf_batch import download_daily_history

        yf_symbols = [_to_yf(s, market) for s in batch]
        # strict=True：两个列表长度必须一致（yf_symbols 是 batch 逐个映射来的）。
        # 万一哪天 _to_yf 变成会过滤元素的实现，这里当场炸而不是静默错位映射
        # —— 错位意味着把 A 股票的行情写进 B 股票，比崩溃可怕得多。
        restore = dict(zip(yf_symbols, batch, strict=True))
        df = download_daily_history(yf_symbols, start)

        out: dict[str, object] = {}
        for yf_sym in yf_symbols:
            orig = restore[yf_sym]
            try:
                sub = df[yf_sym] if len(yf_symbols) > 1 else df
                sub = sub.dropna(how="all")
            except (KeyError, IndexError, TypeError):
                sub = None
            out[orig] = sub
        return out

    @staticmethod
    def _df_to_records(symbol: str, market: str, df) -> list[dict]:
        """DataFrame → bulk_upsert 认的 records。逐字照抄 overseas_job._df_to_records。"""
        records = []
        for idx, row in df.iterrows():
            try:
                records.append({
                    "symbol": symbol, "market": market,
                    "date": idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10],
                    "open": float(row["Open"]), "high": float(row["High"]),
                    "low": float(row["Low"]), "close": float(row["Close"]),
                    "volume": float(row["Volume"]) if row.get("Volume") == row.get("Volume") else 0,
                    "amount": None, "turnover": None,
                })
            except (KeyError, ValueError, TypeError):
                continue
        return records

    def _precheck(self, market: str, start: str) -> bool:
        """连通性预检：拿一只肯定有数据的锚点票探路。

        照抄 overseas_job:255 的做法，理由也一样（那边注释记着一次真实事故）：
        代理挂了会让**每一只**票都拉不到数据，若不预检，就会把「网络坏了」
        误当成「这些票都没数据」。这里虽然不写 confirmed_no_data，但没有预检
        就会在网络坏时白跑 160 批、刷一屏 warning，还可能招 Yahoo 的黑名单。
        """
        from acquisition.markets.yf_batch import fetch_daily_history
        from data_engine.deep_history.overseas_job import SINGLE_FETCH_TIMEOUT, _call_with_timeout

        anchor = _ANCHOR[market]
        try:
            df = _call_with_timeout(
                lambda: fetch_daily_history(_to_yf(anchor, market), start),
                SINGLE_FETCH_TIMEOUT, f"{market} 增量预检",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[港美股增量] {market} 连通性预检异常: {e}")
            return False
        if df is None or df.empty:
            logger.error(
                f"[港美股增量] {market} 连通性预检失败（{anchor} 拉不到数据），"
                f"疑似代理/网络问题，本次不处理任何股票"
            )
            return False
        return True

    def run(self, market: str, *, today: date | None = None,
            limit: int | None = None) -> dict:
        """跑一个市场的增量更新。

        Args:
            market: hk_stock | us_stock
            today: 覆盖「今天」（测试用）
            limit: 只处理前 N 只（调试用）

        Returns:
            `{"market", "universe", "todo", "updated", "rows", "failed", "skipped_*"}`
        """
        if market not in MARKETS:
            raise ValueError(f"不支持的市场: {market}（只接受 {MARKETS}）")
        today = today or date.today()

        session = get_session()
        try:
            todo, latest, stats = self._plan(session, market, today)
            result = {"market": market, "updated": 0, "rows": 0, "failed": 0, **stats}
            if not todo:
                logger.info(f"[港美股增量] {market} 无需更新: {stats}")
                return result
            if limit:
                todo = todo[:limit]

            if not self._precheck(market, stats["start"]):
                result["error"] = "连通性预检失败"
                return result

            logger.info(
                f"[港美股增量] {market} 开跑: {len(todo)} 只待更新（最早起点 {stats['start']}，"
                f"已最新 {stats['skipped_fresh']} 只，无数据跳过 {stats['skipped_no_data']} 只）"
            )

            from data_engine.deep_history.overseas_job import _call_with_timeout

            for i in range(0, len(todo), BATCH_SIZE):
                batch = todo[i:i + BATCH_SIZE]
                # 每批各算各的起点（todo 已按落后程度排序，同批相近）——
                # 用全局起点会让只缺 9 天的票也拉 90 根 bar，白烧 10 倍流量
                batch_start = self._start_for(min(latest[s] for s in batch), today)
                try:
                    got = _call_with_timeout(
                        lambda b=batch, st=batch_start: self._fetch_batch(market, b, st),
                        BATCH_FETCH_TIMEOUT, f"{market} batch{i // BATCH_SIZE}",
                    )
                except Exception as e:  # noqa: BLE001 — 单批失败不拖垮整轮
                    result["failed"] += len(batch)
                    logger.warning(f"[港美股增量] {market} 批 {i // BATCH_SIZE} 失败: {e}")
                    continue

                records: list[dict] = []
                for sym, df in got.items():
                    if df is None or df.empty:
                        continue
                    recs = self._df_to_records(sym, market, df)
                    if recs:
                        records.extend(recs)
                        result["updated"] += 1
                if records:
                    # 幂等：靠 daily_quotes 的 idx_symbol_date 唯一索引 INSERT OR REPLACE，
                    # 重复区间重跑不会重复落行
                    result["rows"] += bulk_upsert_quotes(session, records)

                if i + BATCH_SIZE < len(todo):
                    time.sleep(SLEEP_BETWEEN_BATCHES + random.uniform(0, 1))

            logger.success(
                f"[港美股增量] {market} 完成: 更新 {result['updated']} 只 / "
                f"{result['rows']} 行 / 失败 {result['failed']} 只"
            )
            return result
        finally:
            session.close()


def update_overseas_daily(*, today: date | None = None) -> dict:
    """两个市场都跑一遍 —— 每日链海外侧的入口。

    **抢 Yahoo 互斥锁**：深历史回补也在打 Yahoo，同时跑等于两倍请求量，两边都可能
    被限速。抢不到就跳过本次（定时任务宁可等下一轮，也不要堆在这儿干等）。
    """
    from acquisition.markets.yf_batch import yahoo_job_lock

    with yahoo_job_lock("港美股每日增量") as ok:
        if not ok:
            return {"skipped": "Yahoo 长任务互斥（深历史回补正在跑）"}
        out: dict = {}
        for market in MARKETS:
            try:
                out[market] = OverseasDailyUpdater().run(market, today=today)
            except Exception as e:  # noqa: BLE001 — 一个市场炸了不影响另一个
                logger.warning(f"[港美股增量] {market} 异常: {e}")
                out[market] = {"error": str(e)}
        return out

"""
财务基本面数据获取器 — 新浪财务分析指标

默认走直连 HTTP 解析（不占 akshare 全局锁、可多 worker 并发），
解析失败时逐只回落 akshare 同源接口。输出列与 akshare 版完全一致
（已 diff 验证 parity）。
"""
import io
import os
import queue
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import pandas as pd
from loguru import logger

# 并发抓取的 worker 上限（= 代理池 IP 槽数）。新浪对单源并发容忍度有限，默认保守 4；
# 快代理 IP 充足、限速可控时可用 env 上调（限速换 IP 的兜底逻辑不受影响）。
_MAX_WORKERS = max(1, int(os.getenv("FINANCIAL_FETCH_WORKERS", "4")))

# 新浪财务指标页（与 akshare stock_financial_analysis_indicator 同源）
SINA_INDICATOR_URL = (
    "https://money.finance.sina.com.cn/corp/go.php/vFD_FinancialGuideLine"
    "/stockid/{code}/ctrl/{year}/displaytype/4.phtml"
)
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class FinancialFetcher:
    """A 股财务数据获取器，基于新浪财务分析指标接口"""

    # 新浪列名 → 数据库字段名映射
    COLUMN_MAP = {
        "日期": "report_date",
        "摊薄每股收益(元)": "eps",
        "加权每股收益(元)": "eps_weighted",
        "净资产收益率(%)": "roe",
        "加权净资产收益率(%)": "roe_weighted",
        "总资产利润率(%)": "roa",
        "销售毛利率(%)": "gross_margin",
        "销售净利率(%)": "net_margin",
        "营业利润率(%)": "operating_margin",
        "每股净资产_调整后(元)": "bvps",
        "每股经营性现金流(元)": "ocfps",
        "每股资本公积金(元)": "capital_reserve_ps",
        "每股未分配利润(元)": "undistributed_ps",
        "主营业务收入增长率(%)": "revenue_yoy",
        "净利润增长率(%)": "net_profit_yoy",
        "净资产增长率(%)": "net_asset_yoy",
        "总资产增长率(%)": "total_asset_yoy",
        "流动比率": "current_ratio",
        "速动比率": "quick_ratio",
        "资产负债率(%)": "debt_ratio",
        "股东权益比率(%)": "equity_ratio",
        "存货周转率(次)": "inventory_turnover",
        "存货周转天数(天)": "inventory_turnover_days",
        "应收账款周转天数(天)": "receivable_turnover_days",
        "总资产周转率(次)": "total_asset_turnover",
        "成本费用利润率(%)": "cost_expense_ratio",
        "三项费用比重": "expense_ratio",
        "总资产(元)": "total_assets",
        "主营业务利润(元)": "revenue",
    }

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """
        将标准化 symbol (600519.SH) 转换为纯数字代码 (600519)
        """
        return symbol.split(".")[0]

    # ------------------------------------------------------------------
    # 直连新浪（无 akshare 全局锁，可并发）
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_year_page(html: str) -> Optional[pd.DataFrame]:
        """解析单个年份页面的指标表（第 13 张表，行=指标 列=报告期 → 转置）"""
        try:
            tables = pd.read_html(io.StringIO(html))
            if len(tables) <= 12:
                return None
            t = tables[12]
            t.columns = t.iloc[0].tolist()
            t = t.iloc[1:].set_index("报告日期").transpose().reset_index()
            t = t.rename(columns={"index": "日期"})
            t = t[t["日期"].astype(str).str.match(r"\d{4}-\d{2}-\d{2}")]
            return t if not t.empty else None
        except (ValueError, KeyError, IndexError):
            return None

    def _fetch_sina_indicator_direct(
        self,
        code: str,
        start_year: str,
        proxies: Optional[Dict[str, str]] = None,
        rate_wait: Optional[Callable[[], None]] = None,
    ) -> pd.DataFrame:
        """直连新浪拉取财务指标（返回与 akshare 相同的中文宽表）。

        Args:
            code: 6 位纯数字代码
            start_year: 起始年份
            proxies: 可选代理（并发批量时各 worker 传各自 IP）
            rate_wait: 每次请求前调用的限速钩子

        Raises:
            requests.HTTPError: 456 等限速信号，交由上层换 IP
        """
        from net import make_domestic_session

        session = make_domestic_session(proxies)
        headers = {"User-Agent": _UA}
        try:
            cur_year = datetime.now().year
            if rate_wait:
                rate_wait()
            resp = session.get(
                SINA_INDICATOR_URL.format(code=code, year=cur_year),
                headers=headers, timeout=15,
            )
            resp.raise_for_status()
            resp.encoding = "gbk"

            years = sorted({int(y) for y in re.findall(r"ctrl/(\d{4})/displaytype", resp.text)})
            years = [y for y in years if y >= int(start_year)]

            frames: List[pd.DataFrame] = []
            first_df = self._parse_year_page(resp.text)
            if first_df is not None and cur_year in years:
                frames.append(first_df)
            if cur_year in years:
                years.remove(cur_year)

            for year in years:
                if rate_wait:
                    rate_wait()
                r = session.get(
                    SINA_INDICATOR_URL.format(code=code, year=year),
                    headers=headers, timeout=15,
                )
                r.raise_for_status()
                r.encoding = "gbk"
                df = self._parse_year_page(r.text)
                if df is not None:
                    frames.append(df)

            if not frames:
                return pd.DataFrame()
            out = pd.concat(frames, ignore_index=True)
            return out.drop_duplicates(subset=["日期"])
        finally:
            session.close()

    # ------------------------------------------------------------------
    # 标准化管线
    # ------------------------------------------------------------------

    def _normalize_raw(self, symbol: str, raw: pd.DataFrame) -> pd.DataFrame:
        """中文宽表 → 英文列名标准 DataFrame（COLUMN_MAP 管线）"""
        if raw is None or raw.empty:
            logger.warning(f"{symbol} 无财务数据返回")
            return pd.DataFrame()

        # 只保留有映射的列
        available = {k: v for k, v in self.COLUMN_MAP.items() if k in raw.columns}
        df = raw[list(available.keys())].rename(columns=available)

        # 解析日期
        df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
        df = df.dropna(subset=["report_date"])

        # 数值列转 float
        numeric_cols = [c for c in df.columns if c != "report_date"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按日期降序排列（最新在前）
        df = df.sort_values("report_date", ascending=False).reset_index(drop=True)

        logger.info(f"{symbol} 获取到 {len(df)} 条财务数据 ({df['report_date'].min()} ~ {df['report_date'].max()})")
        return df

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def fetch_financial_data(
        self,
        symbol: str,
        start_year: str = "2015",
        proxies: Optional[Dict[str, str]] = None,
        rate_wait: Optional[Callable[[], None]] = None,
    ) -> pd.DataFrame:
        """
        获取单只股票的历史财务指标数据

        优先直连新浪 HTTP（不占 akshare 全局锁），失败回落 akshare。

        Args:
            symbol: 股票代码，支持 600519.SH 或 600519
            start_year: 起始年份
            proxies: 可选代理配置
            rate_wait: 可选限速钩子（批量并发时传入）

        Returns:
            标准化后的 DataFrame，列名为英文
        """
        pure_code = self._normalize_symbol(symbol)
        logger.info(f"正在获取 {symbol} 的财务数据 (start_year={start_year})...")

        raw = pd.DataFrame()
        try:
            raw = self._fetch_sina_indicator_direct(
                pure_code, start_year, proxies=proxies, rate_wait=rate_wait,
            )
        except Exception as e:
            logger.warning(f"{symbol} 直连新浪失败: {e}，回落 akshare")

        if raw.empty:
            # akshare 兜底（同源数据，占全局锁但仅少量失败股票走这条路）
            try:
                import akshare as ak
                from net import domestic_akshare
                raw = domestic_akshare(
                    ak.stock_financial_analysis_indicator,
                    symbol=pure_code,
                    start_year=start_year,
                )
            except Exception as e:
                logger.error(f"获取 {symbol} 财务数据失败: {e}")
                return pd.DataFrame()

        return self._normalize_raw(symbol, raw)

    def fetch_batch_iter(
        self,
        symbols: List[str],
        start_year: str = "2015",
        workers: int = 3,
    ) -> Iterator[Tuple[Optional[str], Optional[pd.DataFrame]]]:
        """
        批量获取财务数据，按完成顺序逐只 yield (symbol, DataFrame)。

        失败的 symbol 也会 yield（空 DataFrame），保证消费方能对齐进度。
        workers > 1 时用代理池并发（每 worker 独立 IP + 独立限速器，
        新浪限速信号 HTTP 456 自动换 IP）；workers=1 为串行回滚模式。
        worker 内所有异常（含 `pool.acquire()` 本身失败）均在 `_one` 的
        finally 里兜住并恰好 put 一次结果，迭代器必然产出 len(symbols) 个
        终态结果后终止。

        并发模式下偶尔会 yield `(None, None)` 作为心跳标记（消费方长时间
        收不到结果时用于探活，不代表任何 symbol 的数据，调用方应跳过）。
        """
        if workers <= 1 or len(symbols) <= 1:
            for i, sym in enumerate(symbols, 1):
                logger.info(f"[{i}/{len(symbols)}] 获取 {sym} 财务数据...")
                try:
                    df = self.fetch_financial_data(sym, start_year=start_year)
                except Exception as e:
                    logger.warning(f"{sym} 财务获取失败: {e}")
                    df = pd.DataFrame()
                yield sym, df
            return

        from net.proxy_pool import ProxyPool, is_proxy_connect_error
        from data_engine.liveness import LivenessTracker

        workers = min(workers, _MAX_WORKERS)  # 上限可经 FINANCIAL_FETCH_WORKERS 调
        pool = ProxyPool(size=workers, min_delay=0.5, max_delay=3.0)
        if pool.direct_mode:
            logger.warning("无快代理，财务批量退回串行模式")
            yield from self.fetch_batch_iter(symbols, start_year=start_year, workers=1)
            return

        result_q: "queue.Queue[Tuple[str, pd.DataFrame]]" = queue.Queue()

        def _one(sym: str) -> None:
            df = pd.DataFrame()
            slot = None
            try:
                slot = pool.acquire()
                try:
                    df = self.fetch_financial_data(
                        sym, start_year=start_year,
                        proxies=slot.to_requests_proxies(),
                        rate_wait=slot.rate_limiter.wait,
                    )
                    slot.rate_limiter.on_success()
                    pool.report_success(slot)
                except Exception as e:
                    # 456/连接重置等 → 标记 IP 失效，akshare 兜底已在内层做过
                    slot.rate_limiter.on_failure(is_rate_limit=True)
                    pool.report_failure(slot, proxy_connect=is_proxy_connect_error(e))
                    logger.warning(f"{sym} 财务批量获取失败: {e}")
                    df = pd.DataFrame()
            except Exception as e:  # noqa: BLE001 — 连 pool.acquire() 本身失败也不能让该 symbol 永不产出结果
                logger.warning(f"{sym} 财务批量 worker 意外异常: {e}")
            finally:
                if slot is not None:
                    pool.release(slot)
                # 无论成败都恰好入队一次：旧实现里 acquire() 抛异常会跳过这行，
                # 消费侧的定长 range(len(symbols)) 就会永久卡在 result_q.get()
                result_q.put((sym, df))

        tracker = LivenessTracker(stall_timeout=180.0, heartbeat_interval=2.0)
        ex = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fin-batch")
        futures = []
        try:
            for sym in symbols:
                futures.append(ex.submit(_one, sym))

            yielded: set = set()
            while len(yielded) < len(symbols):
                try:
                    sym, df = result_q.get(timeout=0.5)
                except queue.Empty:
                    missing = [s for s in symbols if s not in yielded]
                    if all(f.done() for f in futures) and result_q.empty():
                        if missing:
                            logger.error(f"财务批量：{len(missing)} 只未返回结果（worker 异常退出），按空结果补齐")
                        for s in missing:
                            yield s, pd.DataFrame()
                        break
                    if pool.breaker_state == "dead":
                        logger.error(f"财务批量：代理与直连均不可用，{len(missing)} 只按空结果补齐后中止")
                        for s in missing:
                            yield s, pd.DataFrame()
                        break
                    if tracker.is_stalled():
                        logger.error(f"财务批量：{tracker.stall_timeout:.0f}s 无任何 worker 活动，"
                                     f"{len(missing)} 只按空结果补齐后中止")
                        for s in missing:
                            yield s, pd.DataFrame()
                        break
                    if tracker.should_heartbeat():
                        yield None, None
                        tracker.mark_yield()
                    continue

                tracker.touch()
                yielded.add(sym)
                yield sym, df
                tracker.mark_yield()
        finally:
            # 消费方提前放弃（GeneratorExit）时取消未开跑的任务
            ex.shutdown(wait=False, cancel_futures=True)

    def fetch_batch(
        self,
        symbols: List[str],
        start_year: str = "2015",
        workers: int = 3,
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取多只股票的财务数据（fetch_batch_iter 的聚合版）

        Returns:
            {symbol: DataFrame} 字典（失败的 symbol 不包含）
        """
        results: Dict[str, pd.DataFrame] = {}
        total = len(symbols)
        done = 0
        for sym, df in self.fetch_batch_iter(symbols, start_year=start_year, workers=workers):
            if sym is None:  # 心跳标记，非终态结果，跳过
                continue
            done += 1
            if not df.empty:
                results[sym] = df
            if done % 20 == 0:
                logger.info(f"财务批量进度: {done}/{total}")

        logger.info(f"财务批量完成: {len(results)}/{total} 只成功 (workers={workers})")
        return results

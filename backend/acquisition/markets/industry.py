"""
A 股所属行业 —— **多源链**：东财走得通就走东财，走不通换巨潮。

## 为什么需要源链

`ak.stock_individual_info_em` 打的是 `push2.eastmoney.com/api/qt/stock/get`，
一个**重度 IP 门控**的端点。2026-07-10 实测（每次都用新提取的快代理 IP）：

| 端点 | 新 IP 通过率 |
|---|---|
| 百度 / 腾讯 `qt.gtimg`（对照） | 100% |
| 东财 `qt/ulist.np/get` | 3~6 / 10 |
| 东财 `qt/clist/get` | 波动，最差 0 / 8 |
| 东财 `qt/stock/get`（短字段） | 1 / 14 |
| 东财 `qt/stock/get` + akshare 的 116 字段 | 0 / 14（含直连） |

IP 消耗的驱动是**东财按源 IP 封禁**，不是代理坏了（`requests` 的 `ProxyError`
是误导性标签——urllib3 把目标服务器的拒绝也包成它）。而 `domestic_akshare`
把「东财拒绝这个 IP」当成「IP 坏了」，每调一次白烧 `max_rounds - 1` 个 IP。
`risk_analyzer` 逐只持仓调它，异常再被 `except Exception` 吞成「未知」
——**烧了 IP，一个行业都没查到**。

所以不再赌东财。两个源，一次只用一个：

| 源 | 请求数 | 口径 | IP 消耗 |
|---|---|---|---|
| 东财 `qt/clist/get` 的 `f100` | ~53（翻页） | 申万二级（细，如「银行Ⅱ」） | 高：每页都在赌 IP |
| 巨潮 `stock_profile_cninfo` | 每只 1 个 | 证监会二级（~90 类，如「货币金融服务」） | **0**：直连，不经代理 |

**整轮只用一个源**——两套分类口径混进同一列，行业集中度 HHI 就是垃圾。
`auto` 先用 1 个请求探东财（最多换 2 个 IP），通就走它，不通立刻换巨潮。
"""
import time
from collections.abc import Iterator
from typing import Literal

from loguru import logger

from acquisition.channels import Channel
from acquisition.crawler.base import BaseCrawler
from common.market import add_exchange_suffix, to_bare_code
from net.domestic import ProxyRetriesExhaustedError

IndustrySource = Literal["auto", "eastmoney", "cninfo"]

_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# 深主板 / 创业板 / 沪主板 / 科创板 —— 与 `realtime._fetch_one_page` 同一口径
_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_PAGE_SIZE = 100
_MAX_PAGES = 200          # 5201 只 / 100 ≈ 53 页；留足余量，靠 total 提前收
_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
# 连续这么多页失败 → 判定东财这轮不可用，中止（每页已经换过 8 个 IP 了）
_MAX_CONSECUTIVE_FAILURES = 4

# 巨潮逐只查，礼貌限速；实测 5201 只约 33 分钟、0 个 IP
_CNINFO_INTERVAL = 0.05
_CNINFO_RETRIES = 2
_CNINFO_LOG_EVERY = 100

# 数据源对「没有行业」的表示法各异，都不该当行业名落库
_EMPTY_INDUSTRY = {"", "-", "null", "None", "nan"}


def resolve_source(symbols: list[str] | None = None,
                   *, source: IndustrySource = "auto") -> str:
    """定下这一轮用哪个源。`auto` 用**一个真实请求**（最多换 2 个 IP）探东财。

    探测本身就是一次真实的 clist 调用——预探活的成本等于直接发真请求，所以不另造
    轻量探针，只是把换 IP 预算压到 2，免得东财全面封禁时先烧掉 32 个 IP 才换源。
    """
    if source != "auto":
        if source == "cninfo" and not symbols:
            raise ValueError("巨潮源逐只查询，必须提供 symbols 列表")
        return source
    if _eastmoney_reachable():
        return "eastmoney"
    logger.warning("东财 clist 探测不通（IP 被封），换巨潮源——慢但不烧 IP")
    if not symbols:
        raise ValueError("巨潮源逐只查询，必须提供 symbols 列表")
    return "cninfo"


def stream_a_share_industry(
    symbols: list[str] | None = None,
    *,
    source: IndustrySource = "auto",
    limit: int | None = None,
) -> tuple[str, Iterator[tuple[str, str]]]:
    """**流式**产出 `(symbol, industry)`，让调用方边收边落库。

    全市场逐只查要跑半小时。一次性攒完再写库意味着：过程完全不可观测，
    中途挂掉半小时白费。所以取数改成生成器，`industry_updater` 分批 commit。

    Args:
        symbols: 带后缀的 symbol 列表。**巨潮源必填**；东财源忽略（它翻全市场页）。
        source: `auto` 先探东财、不通换巨潮；也可强制指定单一源。
        limit: 只取前 N 只（调试用）。

    Returns:
        `(source_used, iterator)`。**源在迭代开始前就定好**——调用方据此决定
        要不要清掉旧口径的数据（两个源的分类体系不同，混在一列里 HHI 就是垃圾）。

    Raises:
        ProxyQuotaExhaustedError: 东财源且快代理额度耗尽。
        ValueError: 用巨潮源却没给 `symbols`。
    """
    used = resolve_source(symbols, source=source)
    if used == "eastmoney":
        return used, _iter_eastmoney(limit=limit)
    return used, _iter_cninfo(symbols or [], limit=limit)


def fetch_a_share_industry_map(
    symbols: list[str] | None = None,
    *,
    source: IndustrySource = "auto",
    limit: int | None = None,
) -> tuple[dict[str, str], str]:
    """`stream_a_share_industry` 的一次性 dict 版（小批量/测试用）。

    Returns:
        `(mapping, source_used)`。取不到行业的票**不进 mapping**（而非填「未知」）
        ——调用方据此区分「没查到」和「数据源说没有」。
    """
    used, it = stream_a_share_industry(symbols, source=source, limit=limit)
    return dict(it), used


# ── 东财：qt/clist/get 的 f100 ────────────────────────────────────────────────

def _clist_params(page: int, page_size: int) -> dict[str, str]:
    return {
        "pn": str(page), "pz": str(page_size), "po": "0", "np": "1",
        "ut": _EM_UT, "fltt": "2", "invt": "2", "fid": "f12",
        "fs": _A_SHARE_FS, "fields": "f12,f14,f100",
    }


def _eastmoney_reachable() -> bool:
    """一个请求（最多换 2 个 IP）探东财通不通。

    探测本身就是一次真实的 clist 调用——**预探活的成本等于直接发真请求**，
    所以这里不额外造一个轻量探针，只是把换 IP 预算压到 2，
    免得在东财全面封禁时先烧掉 32 个 IP 才知道该换源。
    """
    crawler = BaseCrawler(channel=Channel.DOMESTIC, timeout=8, max_rounds=2)
    try:
        data = crawler.get_json(_CLIST_URL, params=_clist_params(1, 20))
    except ProxyRetriesExhaustedError:
        return False
    return bool(data and data.get("rc") == 0 and data.get("data"))


def _iter_eastmoney(*, limit: int | None = None,
                    page_size: int = _PAGE_SIZE) -> Iterator[tuple[str, str]]:
    """翻 clist 全市场页，逐只产出 `(symbol, 申万二级行业)`。

    单页失败**跳过继续**；连续失败到阈值才判定东财不可用并中止（保留已产出的）。
    """
    crawler = BaseCrawler(channel=Channel.DOMESTIC, timeout=8, max_rounds=8,
                          min_interval=0.15)
    yielded = 0
    total = 0
    failed_pages: list[int] = []
    consecutive_failures = 0

    for page in range(1, _MAX_PAGES + 1):
        try:
            data = crawler.get_json(_CLIST_URL, params=_clist_params(page, page_size))
        except ProxyRetriesExhaustedError as e:
            # 这一页换满 IP 仍全败。**只跳过这一页**——「一个 IP 都取不到」是另一个
            # 子类（ProxyQuotaExhaustedError），它会往上抛，整个任务放弃。
            logger.warning(f"东财行业第 {page} 页换满 IP 仍失败：{str(e)[:60]}")
            data = None

        # 「取不到这一页」和「翻到底了」是两回事：后者只由 rows 为空 / total 判定。
        if not data or data.get("rc") != 0 or not data.get("data"):
            failed_pages.append(page)
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                logger.error(f"东财行业连续 {consecutive_failures} 页失败，中止"
                             f"（已产出 {yielded} 只）")
                break
            continue
        consecutive_failures = 0

        total = data["data"].get("total") or total
        rows = data["data"].get("diff") or []
        if not rows:
            break                        # 翻到底了

        for row in rows:
            symbol = _safe_symbol(row.get("f12"))
            industry = _clean_industry(row.get("f100"))
            if symbol and industry:
                yield symbol, industry
                yielded += 1
                if limit and yielded >= limit:
                    return

        if total and page * page_size >= total:
            break                        # 翻到底了

    if failed_pages:
        logger.warning(f"东财行业有 {len(failed_pages)} 页取失败: {failed_pages[:10]}"
                       f"{' …' if len(failed_pages) > 10 else ''}")
    logger.info(f"东财行业拉取完成：{yielded} 只 / 东财报 total={total}")


# ── 巨潮：stock_profile_cninfo 的「所属行业」 ─────────────────────────────────

def _iter_cninfo(symbols: list[str], *,
                 limit: int | None = None) -> Iterator[tuple[str, str]]:
    """逐只查巨潮公司概况，产出 `(symbol, 证监会二级行业)`。

    `use_proxy_pool=False` 是**明示直连**，不是静默降级：
    ① 巨潮不做 IP 门控，直连稳定（实测 15/15 + 全市场 5180/5201），走代理纯属浪费额度；
    ② 更要紧的是 `domestic_akshare` 把「空 DataFrame」当失败去换 IP，而退市/
       无资料的票**本来就返回空**（实测 21 只 `*ST` 全是这种）——挂上代理池会为
       它们白烧 `max_rounds` 个 IP。

    实测：5201 只约 33 分钟、0 个 IP。进度每 `_CNINFO_LOG_EVERY` 只打一条。
    """
    import akshare as ak

    from net.domestic import domestic_akshare

    if not symbols:
        raise ValueError("巨潮源逐只查询，必须提供 symbols 列表")
    todo = symbols[:limit] if limit else symbols

    got = 0
    failed: list[str] = []
    started = time.time()
    for i, sym in enumerate(todo, 1):
        code = to_bare_code(sym)
        df = None
        for attempt in range(_CNINFO_RETRIES):
            try:
                df = domestic_akshare(ak.stock_profile_cninfo, symbol=code,
                                      use_proxy_pool=False)
                break
            except Exception as e:  # noqa: BLE001  # 单只失败不该炸掉整批
                if attempt == _CNINFO_RETRIES - 1:
                    failed.append(sym)
                    logger.debug(f"巨潮取 {sym} 失败: {type(e).__name__}: {str(e)[:50]}")
                else:
                    time.sleep(0.5)

        if df is not None and not df.empty:
            industry = _clean_industry(df.iloc[0].get("所属行业"))
            if industry:
                yield sym, industry
                got += 1

        if i % _CNINFO_LOG_EVERY == 0:
            logger.info(f"巨潮行业 {i}/{len(todo)}（拿到 {got}，失败 {len(failed)}）"
                        f" {_eta(i, len(todo), started)}")
        time.sleep(_CNINFO_INTERVAL)

    if failed:
        logger.warning(f"巨潮行业有 {len(failed)} 只取失败: {failed[:10]}"
                       f"{' …' if len(failed) > 10 else ''}")
    logger.info(f"巨潮行业拉取完成：{got}/{len(todo)} 只，耗时 {(time.time()-started)/60:.1f} 分钟")


def _eta(done: int, total: int, started: float) -> str:
    """`[41% | 2138/5201 | 已用 13.4min | 剩 19.2min]`"""
    elapsed = time.time() - started
    rate = done / elapsed if elapsed > 0 else 0
    remain = (total - done) / rate / 60 if rate > 0 else 0
    return (f"[{done / total * 100:.0f}% | 已用 {elapsed / 60:.1f}min "
            f"| 剩约 {remain:.1f}min]")


# ── 共用 ────────────────────────────────────────────────────────────────────

def _clean_industry(raw: object) -> str | None:
    """各源对「没有行业」的表示法不同（东财给 `-`，巨潮给空/NaN）。"""
    s = str(raw or "").strip()
    return None if s in _EMPTY_INDUSTRY else s


def _safe_symbol(raw: object) -> str | None:
    """裸码补后缀；单只脏码（ETF 段、权证）不该炸掉整张表。"""
    try:
        return add_exchange_suffix(str(raw or "").strip())
    except ValueError as e:
        logger.debug(f"跳过无法识别的代码 {raw!r}: {e}")
        return None

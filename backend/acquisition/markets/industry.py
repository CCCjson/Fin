"""
A 股所属行业（申万二级）—— 从东财 `qt/clist/get` 的 `f100` 字段全市场翻页取。

## 为什么不用 akshare

`ak.stock_individual_info_em` 打的是 `push2.eastmoney.com/api/qt/stock/get`。
**该端点已被东财封杀**（2026-07-10 实测：同一个 Session、同一个 IP 上
`ulist.np/get` 返 200，它却连 TLS 都建不起来；换 5 个新 IP、加 `ut`、加 UA、
减字段、直连，全失败）。

更糟的是 `domestic_akshare` 把「端点死了」当成「IP 死了」，于是每调一次
白烧 `max_rounds - 1` 个快代理 IP。`risk_analyzer` 逐只持仓调它，异常被
`except Exception` 吞成「未知」——**烧了 IP，一个行业都没查到**。

`qt/clist/get` 是活的（全市场行情翻页一直在用它），`f100` 就是所属行业。
一次翻 ~53 页拿到全市场，落库后风控查行业零出网。

## 为什么不用 `domestic_rotate`

53 页各自**独立且幂等**：第 30 页失败没必要把前 29 页重抓一遍。
`domestic_rotate` 是给「N 个子请求凑成一份快照、必须原子」的场景用的。
这里每页自己换 IP 重试即可（`BaseCrawler` 内部就是 `domestic_bounded_get`）。
"""

from loguru import logger

from acquisition.channels import Channel
from acquisition.crawler.base import BaseCrawler
from common.market import add_exchange_suffix
from net.domestic import ProxyRetriesExhaustedError

_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
# 深主板 / 创业板 / 沪主板 / 科创板 —— 与 `realtime._fetch_one_page` 同一口径
_A_SHARE_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
_PAGE_SIZE = 100
_MAX_PAGES = 200          # 5201 只 / 100 ≈ 53 页；留足余量，靠 total 提前收
_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
# 连续这么多页失败 → 判定代理池整体不可用，中止（每页已经换过 8 个 IP 了）
_MAX_CONSECUTIVE_FAILURES = 4

# 东财对退市/无行业的票给 "-"，别把它当行业名落库
_EMPTY_INDUSTRY = {"", "-", "null", "None"}


def fetch_a_share_industry_map(
    *,
    limit: int | None = None,
    page_size: int = _PAGE_SIZE,
) -> dict[str, str]:
    """全市场 A 股 `symbol -> 所属行业`（申万二级，如「银行Ⅱ」「航空机场」）。

    单页失败**跳过继续**，不放弃整批：快代理池里死 IP 比例不低，为一页拖垮
    全市场回补不划算，缺的下次跑补上（行业几乎不变，回补是低频任务）。

    Args:
        limit: 只取前 N 只（调试用）。None = 全市场。
        page_size: 每页条数。

    Returns:
        `{"600000.SH": "银行Ⅱ", ...}`。取不到行业的票**不进 dict**（而非填「未知」）
        ——调用方据此区分「没查到」和「东财说没有」。

    Raises:
        ProxyQuotaExhaustedError: 配了快代理却一个 IP 都取不到（额度耗尽）。硬失败，
            继续翻页毫无意义。
            （`ProxyRetriesExhaustedError`——某页换满 IP 仍全败——**不抛**，跳过该页。）
    """
    # min_interval：连打无间隔会被掐连接。max_rounds 给到 8——快代理发的 IP 有相当
    # 比例一拿到就是死的，5 轮不够穿过连续几个死 IP。
    crawler = BaseCrawler(channel=Channel.DOMESTIC, timeout=8, max_rounds=8, min_interval=0.15)
    out: dict[str, str] = {}
    total = 0
    failed_pages: list[int] = []
    consecutive_failures = 0

    for page in range(1, _MAX_PAGES + 1):
        try:
            data = crawler.get_json(_CLIST_URL, params={
                "pn": str(page),
                "pz": str(page_size),
                "po": "0",
                "np": "1",
                "ut": _EM_UT,
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": _A_SHARE_FS,
                "fields": "f12,f14,f100",
            })
        except ProxyRetriesExhaustedError as e:
            # 这一页换满 IP 仍全败。**只跳过这一页**——「一个 IP 都取不到」是另一个
            # 子类（ProxyQuotaExhaustedError），它会往上抛，整个任务放弃。
            logger.warning(f"行业映射第 {page} 页换满 IP 仍失败：{str(e)[:60]}")
            data = None

        # 「取不到这一页」和「翻到底了」是两回事：后者只由 rows 为空 / total 判定。
        if not data or data.get("rc") != 0 or not data.get("data"):
            failed_pages.append(page)
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                logger.error(f"行业映射连续 {consecutive_failures} 页失败，判定代理池不可用，"
                             f"中止（已拿 {len(out)} 只）")
                break
            continue
        consecutive_failures = 0

        total = data["data"].get("total") or total
        rows = data["data"].get("diff") or []
        if not rows:
            break                        # 翻到底了

        for row in rows:
            industry = str(row.get("f100") or "").strip()
            if industry in _EMPTY_INDUSTRY:
                continue
            try:
                symbol = add_exchange_suffix(str(row.get("f12") or "").strip())
            except ValueError as e:
                # 单只脏码不该炸掉整张表（退市整理、权证等偶发）
                logger.debug(f"跳过无法识别的代码 {row.get('f12')!r}: {e}")
                continue
            out[symbol] = industry

        if limit and len(out) >= limit:
            break
        if total and page * page_size >= total:
            break                        # 翻到底了

    if failed_pages:
        logger.warning(f"行业映射有 {len(failed_pages)} 页取失败: {failed_pages[:10]}"
                       f"{' …' if len(failed_pages) > 10 else ''}")
    logger.info(f"行业映射拉取完成：{len(out)} 只有行业 / 东财报 total={total}")
    return dict(list(out.items())[:limit]) if limit else out

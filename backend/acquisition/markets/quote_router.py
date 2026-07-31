"""点/批量实时行情的**跨市场**统一出口（S6 之前只管 A 股）。

`fetch_quotes(symbols)` 按 symbol 推市场分发：沪深走下面这条多源链，
港股/美股/crypto 走各自 fetcher 的 `fetch_realtime`，输出归一到同一个形状。
`fetch_quotes_detailed` 额外给出**缺失清单**（取不到 ≠ 闭市没取，两者分开报）。

沪深链按优先级逐级取数、**逐 symbol 补缺口**，拿全即收工：

    1. 腾讯 qt.gtimg   —— 批量 ~500/请求，字段最全，实测最稳
    2. 新浪 hq.sinajs  —— 批量 ~800/请求，缺 PE/PB/市值
    3. 百度 股市通     —— 单只/请求，只给前两源都漏掉的零星几只兜底

三个源**全都直连友好**，故意不含东财：东财要烧快代理 IP，而腾讯已给出等价富字段；
凡三源皆无的标的几乎必是退市/无效码，东财去追也只会白烧 IP。东财留作全市场翻页源
（`realtime.fetch_a_share_realtime`）。

**连接策略统一（Jason 拍板）**：每个源都 `prefer_direct=True`——先直连，直连失败
才换快代理随机 IP。这不是降级：直连是显式点头的第 0 轮，失败后一律走代理，取不到
IP 照抛 `ProxyExhaustedError`（铁律，见 `net._rotate` / CODING_STANDARDS §8）。正常
情况腾讯直连一把拿全，后两源与代理都不触发，IP 消耗趋近于零。

输出口径 = `realtime.fetch_quotes_by_symbols` 的历史形状（`change_percent` / `change`
/ `is_index` / `timestamp`），实盘监控（position_guardian / price_alert_monitor /
intraday）全靠这几个键，三个源的原始形状都在此归一。

**数据血缘（2026-07-17，P0-2）**：每行额外带 `source`（哪个源出的）与 `as_of`
（**源自报的报价时刻**，不是抓取时刻 `timestamp`）。缺失字段一律 None，不再用 0
顶替 —— 详见 `_canonical` 的 docstring。
"""
from collections.abc import Callable
from datetime import datetime
from typing import Any

from loguru import logger

from acquisition.markets.china_batch_quotes import (
    SINA_BATCH_SIZE,
    SINA_URL,
    TENCENT_BATCH_SIZE,
    TENCENT_URL,
    _parse_sina_line,
    _parse_tencent_line,
    _to_prefixed,
)
from common.market import A_SHARE
from net import ProxyExhaustedError, domestic_rotate, make_domestic_session

# 源执行体：收 (symbols, proxies) 返回 canonical 行；单参闭包给 domestic_rotate。
SourceRunner = Callable[[list[str], "dict[str, str] | None"], list[dict[str, Any]]]
_Batch = Callable[["dict[str, str] | None"], list[dict[str, Any]]]

# 每源最多换几轮 IP：第 0 轮直连 + 至多 1 轮代理。链路的真正冗余靠**多源**，
# 不靠单源死磕换 IP，所以这里压得很低。
_MAX_ROUNDS_PER_SOURCE = 2

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
}

# 东财匿名公开 token（非密钥，全网通用）
_EM_UT = "bd1d9ddb04089700cf9c27f6f7426281"
_INDEX_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_INDEX_FIELDS = "f2,f3,f4,f6,f12,f14"


def _num(v: Any, cast: Callable = float, default: Any = None) -> Any:
    if v is None or v == "" or v == "-":
        return default
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


def _looks_like_index(symbol: str) -> bool:
    """沪市 000xxx.SH / 深市 399xxx.SZ 视为指数（仅用于打 is_index 标记）。"""
    code = symbol[:6]
    return (symbol.endswith(".SH") and code.startswith("000")) or \
           (symbol.endswith(".SZ") and code.startswith("399"))


def _canonical(symbol: str, *, name: Any = None, price: Any = None,
               change: Any = None, change_percent: Any = None, volume: Any = None,
               amount: Any = None, open_: Any = None, high: Any = None,
               low: Any = None, prev_close: Any = None, amplitude: Any = None,
               turnover: Any = None, source: str | None = None,
               as_of: str | None = None) -> dict[str, Any]:
    """归一到 `fetch_quotes_by_symbols` 的历史输出形状。

    **缺失字段一律留 None，绝不用 0 顶替**（2026-07-17，P0-2）。此前
    `change`/`change_percent`/`volume`/`open`/`high`/`low` 都写着
    `x if x is not None else 0`，注释说是「归一到历史形状」——代价是
    **「今天平盘」与「源没返回这个字段」在归一化的第一跳就不可分辨**。
    下游拿着一个 0 无从判断该信还是该疑，涨幅预警因此静默失效
    （`0 >= 5%` 永远不触发，而且看不出来）。

    `as_of` = **源自报的报价时刻**；`timestamp` = 我们的抓取时刻。两者必须
    分开：实测 17:49 抓到的行新浪自报 15:34，混为一谈等于凭空把收盘价说新
    两小时。源没给 `as_of` 就是 None —— **不许拿 `timestamp` 顶替**。
    """
    return {
        "symbol": symbol,
        "name": name or "",
        "price": price,
        "change": change,
        "change_percent": change_percent,
        "volume": volume,
        "amount": amount,
        "open": open_,
        "high": high,
        "low": low,
        "prev_close": prev_close,
        "amplitude": amplitude,
        "turnover": turnover,
        "source": source,
        "as_of": as_of,
        "timestamp": datetime.now().isoformat(),
        "is_index": _looks_like_index(symbol),
    }


def _from_china_batch(row: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    """腾讯/新浪解析器的形状（change_pct/change_amount）→ canonical。"""
    return _canonical(
        row["symbol"], name=row.get("name"), price=row.get("price"),
        change=row.get("change_amount"), change_percent=row.get("change_pct"),
        volume=row.get("volume"), amount=row.get("amount"),
        open_=row.get("open"), high=row.get("high"), low=row.get("low"),
        prev_close=row.get("prev_close"), amplitude=row.get("amplitude"),
        turnover=row.get("turnover"), source=source,
        as_of=row.get("quote_time"),
    )


# ---- 四个源的单轮执行体：收 proxies，任一硬失败就 raise（→ 换 IP 重来） ----

def _run_tencent(symbols: list[str], proxies: dict[str, str] | None) -> list[dict[str, Any]]:
    prefixed = [p for p in (_to_prefixed(s) for s in symbols) if p]
    if not prefixed:
        return []
    out: list[dict[str, Any]] = []
    session = make_domestic_session(proxies)
    try:
        for i in range(0, len(prefixed), TENCENT_BATCH_SIZE):
            batch = prefixed[i:i + TENCENT_BATCH_SIZE]
            resp = session.get(TENCENT_URL + ",".join(batch), headers=_HEADERS, timeout=8)
            resp.encoding = "gbk"
            for line in resp.text.splitlines():
                row = _parse_tencent_line(line)
                if row and row.get("price"):
                    out.append(_from_china_batch(row, "tencent"))
    finally:
        session.close()
    return out


def _run_sina(symbols: list[str], proxies: dict[str, str] | None) -> list[dict[str, Any]]:
    prefixed = [p for p in (_to_prefixed(s) for s in symbols) if p]
    if not prefixed:
        return []
    headers = {**_HEADERS, "Referer": "https://finance.sina.com.cn"}
    out: list[dict[str, Any]] = []
    session = make_domestic_session(proxies)
    try:
        for i in range(0, len(prefixed), SINA_BATCH_SIZE):
            batch = prefixed[i:i + SINA_BATCH_SIZE]
            resp = session.get(SINA_URL + ",".join(batch), headers=headers, timeout=8)
            resp.encoding = "gbk"
            for line in resp.text.splitlines():
                row = _parse_sina_line(line)
                if row and row.get("price"):
                    out.append(_from_china_batch(row, "sina"))
    finally:
        session.close()
    return out


def _run_baidu(symbols: list[str], proxies: dict[str, str] | None) -> list[dict[str, Any]]:
    """百度股市通，**单只/请求**——只给前两源都漏的零星几只兜底，别拿它跑全市场。"""
    url = "https://finance.pae.baidu.com/vapi/v1/getquotation"
    headers = {**_HEADERS, "Referer": "https://gushitong.baidu.com/"}
    out: list[dict[str, Any]] = []
    session = make_domestic_session(proxies)
    try:
        for symbol in symbols:
            if "." not in symbol:
                continue
            code = symbol.split(".", 1)[0]
            params = {"srcid": "5353", "pointType": "string",
                      "group": "quotation_minute_ab", "query": code, "code": code,
                      "market_type": "ab", "newFormat": "1"}
            # session.get 的 RequestException 往上抛 → _rotate 换 IP（网络真失败）。
            # 但 resp.json() 对空响应抛 ValueError = 「这只码百度没有」，跳过即可，
            # 绝不为一个退市/无效码去买 IP（那正是烧 IP 的病）。
            resp = session.get(url, params=params, headers=headers, timeout=8)
            try:
                payload = resp.json()
            except ValueError:
                continue
            row = _parse_baidu(symbol, payload)
            if row:
                out.append(row)
    finally:
        session.close()
    return out


def _parse_baidu(symbol: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    result = payload.get("Result") or {}
    cur = result.get("cur") or {}
    price = _num(cur.get("price"))
    if not price:
        return None
    pankou = {x.get("ename"): x.get("originValue")
              for x in result.get("pankouinfos", {}).get("list", [])}
    ratio = cur.get("ratio")  # "-0.41%"
    change_pct = _num(ratio.rstrip("%")) if isinstance(ratio, str) else _num(ratio)
    return _canonical(
        symbol, name=(result.get("basicinfos") or {}).get("name"),
        price=price, change=_num(cur.get("increase")), change_percent=change_pct,
        # 默认值一律 None 不是 0 —— 同 `_canonical` 的理由：百度没给的字段
        # 说成 0 就是拿「缺失」冒充「真的是零」。
        volume=_num(cur.get("volume"), int), amount=_num(cur.get("amount"), float),
        open_=_num(pankou.get("open"), float), high=_num(pankou.get("high"), float),
        low=_num(pankou.get("low"), float), prev_close=_num(pankou.get("preClose")),
        amplitude=_num(pankou.get("amplitudeRatio")), turnover=_num(pankou.get("turnoverRatio")),
        # 百度这个接口不给报价时刻 → as_of 就是 None（诚实地说「不知道几点的」）。
        source="baidu",
    )


# 全直连源,按覆盖/速度排序。**故意不含东财**：它要烧快代理 IP，而腾讯已给出等价
# 富字段；凡腾讯+新浪+百度三个直连源都没有的标的，几乎必是退市/无效码，东财去追
# 也只会 rc=102 白烧一个 IP（position_guardian 每 30s 轮询有一只退市持仓 = 每轮一个
# IP，正是烧掉近万 IP 的那个病）。东财留作全市场翻页源（realtime.fetch_a_share_realtime）。
_SOURCES: list[tuple[str, SourceRunner]] = [
    ("腾讯", _run_tencent),
    ("新浪", _run_sina),
    ("百度", _run_baidu),
]


def _dispatch(runner: SourceRunner, want: list[str]) -> _Batch:
    """把 (runner, want) 绑成 domestic_rotate 要的 `run(proxies)` 单参闭包。"""
    def _call(proxies: dict[str, str] | None) -> list[dict[str, Any]]:
        return runner(want, proxies)
    return _call


def fetch_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    """按 symbols 取**四个市场**的实时行情，归一到同一个 canonical 形状。

    🔴 **S6 之前这个函数只认 `.SH/.SZ`**，其余 symbol 在第一行就被静默过滤掉。
    而它是四个功能共用的取价出口，于是港股/美股/crypto 在这四处全部静默降级：

    | 消费方 | 症状 |
    |---|---|
    | `price_alert_monitor` | 预警**不告警、不报错、不写日志** |
    | `position_guardian` | **止损守护全瞎**（这是风控，比预警更严重） |
    | `portfolio_tools` | 盘中持仓估值退回 EOD |
    | `intraday_tools` | 盘中查价拿不到 |

    每个市场其实都有 `fetch_realtime`，缺的**只是这个路由分发**。

    Returns:
        canonical 形状的行情列表（`symbol` 唯一）。
        ⚠️ **取不到的 symbol 不会静默消失**：走 `fetch_quotes_detailed` 能拿到
        缺失清单。这个函数保持只返回列表，是为了不动四个既有调用方的签名。
    """
    return fetch_quotes_detailed(symbols)["quotes"]


def fetch_quotes_detailed(symbols: list[str]) -> dict[str, Any]:
    """同 `fetch_quotes`，但**把缺失说出来**。

    ⛔ 「少一行」是这一整张卡要消灭的东西：四个消费方现在的 `continue` /
    `if not quote` 全是在替静默缺失兜底，而兜底的结果就是 P1-4 记录的
    「不告警、不报错、不写日志」。

    Returns:
        `{quotes, missing, skipped_closed, by_market}`
        —— `skipped_closed` = 因为市场闭市**刻意没去取**的（不是失败）。
    """
    from common.market import infer_market_from_symbol

    # ⚠️ 惰性 import 是**测试依赖的**：`tests/acquisition/test_quote_router_multimarket.py`
    # patch 的是 `common.market_session.is_open`。改成模块级 `from ... import is_open`
    # 会让那些 patch 静默失效，测试转而读真实时钟 → 变成看日期的 flaky。
    from common.market_session import is_open

    buckets: dict[str, list[str]] = {}
    for s in symbols or []:
        # 去重：同一只票传两次不该发两次请求、也不该在计数里翻倍
        # （`_fetch_a_share` 内部用 dict 天然去重，非 A 股路径没有）。
        m = infer_market_from_symbol(s)
        if s not in buckets.setdefault(m, []):
            buckets[m].append(s)

    quotes: list[dict[str, Any]] = []
    missing: list[str] = []
    skipped: list[str] = []
    by_market: dict[str, int] = {}
    for market, syms in buckets.items():
        # ⭐ **闭市判定对 A 股同样适用**：周末/盘后每次调用照打腾讯→新浪→百度三源，
        # 而且「A 股休市」会被报成「取不到行情、本轮没有被检查」—— 那句话是错的。
        if not is_open(market):
            logger.debug(f"实时行情：{market} 闭市，跳过 {len(syms)} 只（不是失败）")
            skipped.extend(syms)
            by_market[market] = 0
            continue
        got = _fetch_a_share(syms) if market == A_SHARE else _fetch_via_fetcher(market, syms)
        quotes.extend(got)
        by_market[market] = len(got)
        done = {q.get("symbol") for q in got}
        missing.extend(s for s in syms if s not in done)
    if missing:
        logger.warning(f"实时行情：{len(missing)} 只没取到（例 {sorted(missing)[:5]}）")
    return {"quotes": quotes, "missing": sorted(missing),
            "skipped_closed": sorted(skipped), "by_market": by_market}


def _fetch_via_fetcher(market: str, symbols: list[str]) -> list[dict]:
    """非 A 股走各市场自己的 `fetch_realtime`，并归一到 canonical。

    ⚠️ 闭市判定在调用方（`fetch_quotes_detailed`）统一做 —— 四个市场一视同仁，
    别在这里再来一份。
    """
    try:
        # 同层取 fetcher（`acquisition.markets.factory`），⛔ 别去 import
        # `data_engine` —— 那是反向依赖（`engines → acquisition → common/net`）。
        from acquisition.markets.factory import FetcherFactory
        rows = FetcherFactory.create(market).fetch_realtime(symbols) or []
    except ProxyExhaustedError as e:
        # 代理耗尽 ≠ 抓取失败：一个请求都没发出去，重试也没用（铁律：绝不降级直连）。
        logger.warning(f"实时行情/{market}：代理耗尽，{len(symbols)} 只未取到: {e}")
        return []
    except Exception as e:  # noqa: BLE001 — 一个市场取不到不该掀翻其它市场
        logger.warning(f"实时行情/{market} 取价失败: {e}")
        return []
    out = []
    for r in rows:
        sym = r.get("symbol")
        if not sym:
            continue
        out.append(_canonical(
            sym, name=r.get("name"), price=r.get("price"),
            change=r.get("change"),
            # yfinance 那条链用的是 `change_percent`，crypto 也统一成它；
            # 但历史上还有 `change_pct` 的写法，两个都认，别让下游拿到 None。
            change_percent=r.get("change_percent", r.get("change_pct")),
            volume=r.get("volume"), amount=r.get("amount"),
            open_=r.get("open"), high=r.get("high"), low=r.get("low"),
            prev_close=r.get("prev_close"),
            source=market, as_of=r.get("as_of")))
    return out


def _fetch_a_share(symbols: list[str]) -> list[dict[str, Any]]:
    """沪深链：腾讯→新浪→东财→百度，逐 symbol 补缺口。**这条链一个字没动。**

    指数靠 secid / sh-sz 前缀天然消歧（000001.SH 上证指数 vs 000001.SZ 平安银行 不串号）。
    """
    targets = [s for s in symbols if _to_prefixed(s)]
    if not targets:
        return []

    result: dict[str, dict[str, Any]] = {}
    missing = set(targets)

    for name, runner in _SOURCES:
        if not missing:
            break
        want = sorted(missing)
        try:
            rows = domestic_rotate(
                _dispatch(runner, want),
                what=f"实时行情/{name}",
                prefer_direct=True,
                max_rounds=_MAX_ROUNDS_PER_SOURCE,
            )
        except ProxyExhaustedError:
            # 该源直连失败且换不到 IP。下一个源仍会先试直连（不同主机可能就通），
            # 这不是静默降级——每个源都各自走 prefer_direct。全链都挂才返回部分。
            logger.warning(f"实时行情/{name}：直连失败且代理耗尽，转下一源")
            continue
        got = 0
        for r in rows or []:
            sym = r.get("symbol")
            if sym and sym in missing:
                result[sym] = r
                missing.discard(sym)
                got += 1
        if got:
            logger.info(f"实时行情/{name}：补齐 {got} 只，剩 {len(missing)} 只待下一源")

    if missing:
        logger.warning(f"实时行情：{len(missing)} 只全源未取到（例 {sorted(missing)[:5]}）")
    return list(result.values())


def fetch_index_snapshot(secids: list[str]) -> list[dict[str, Any]]:
    """一处取东财指数快照（ulist.np），四方（market_tools/realtime/review/web_searcher）共用。

    `secids` 用东财「市场.代码」前缀，如 `1.000001`（上证）、`100.HSI`（恒生）。前缀
    自带市场，指数不与个股串号。先直连、失败才换快代理随机 IP（铁律，同 S3）。

    Returns:
        `[{code, name, price, change_pct, change_amount, amount}]`；全轮失败或代理耗尽
        返回空列表（调用方按空=稍后重试）。
    """
    if not secids:
        return []

    def _run(proxies: dict[str, str] | None) -> list[dict[str, Any]]:
        session = make_domestic_session(proxies)
        try:
            resp = session.get(_INDEX_URL, params={
                "fltt": 2, "invt": 2, "fields": _INDEX_FIELDS,
                "secids": ",".join(secids), "ut": _EM_UT,
            }, headers=_HEADERS, timeout=10)
            data = resp.json()
        finally:
            session.close()
        # rc!=0 = 被东财挡了（HTTP 200 但业务失败）：抛出去换 IP，别当空结果。
        if data.get("rc") != 0 or not data.get("data"):
            raise RuntimeError(f"东财指数 rc={data.get('rc')}")
        return [{"code": it.get("f12"), "name": it.get("f14"), "price": it.get("f2"),
                 "change_pct": it.get("f3"), "change_amount": it.get("f4"),
                 "amount": it.get("f6")}
                for it in data["data"].get("diff", [])]

    try:
        rows = domestic_rotate(_run, what="指数快照", prefer_direct=True,
                               max_rounds=_MAX_ROUNDS_PER_SOURCE)
    except ProxyExhaustedError:
        logger.warning("指数快照：直连失败且代理耗尽")
        return []
    return rows or []

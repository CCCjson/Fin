"""点/批量 A股实时行情的多源故障转移收口。

`fetch_quotes(symbols)` 按优先级链逐级取数、**逐 symbol 补缺口**，拿全即收工：

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
               turnover: Any = None) -> dict[str, Any]:
    """归一到 `fetch_quotes_by_symbols` 的历史输出形状。"""
    return {
        "symbol": symbol,
        "name": name or "",
        "price": price,
        "change": change if change is not None else 0,
        "change_percent": change_percent if change_percent is not None else 0,
        "volume": volume if volume is not None else 0,
        "amount": amount if amount is not None else 0,
        "open": open_ if open_ is not None else 0,
        "high": high if high is not None else 0,
        "low": low if low is not None else 0,
        "prev_close": prev_close,
        "amplitude": amplitude,
        "turnover": turnover,
        "timestamp": datetime.now().isoformat(),
        "is_index": _looks_like_index(symbol),
    }


def _from_china_batch(row: dict[str, Any]) -> dict[str, Any]:
    """腾讯/新浪解析器的形状（change_pct/change_amount）→ canonical。"""
    return _canonical(
        row["symbol"], name=row.get("name"), price=row.get("price"),
        change=row.get("change_amount"), change_percent=row.get("change_pct"),
        volume=row.get("volume"), amount=row.get("amount"),
        open_=row.get("open"), high=row.get("high"), low=row.get("low"),
        prev_close=row.get("prev_close"), amplitude=row.get("amplitude"),
        turnover=row.get("turnover"),
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
                    out.append(_from_china_batch(row))
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
                    out.append(_from_china_batch(row))
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
        volume=_num(cur.get("volume"), int, 0), amount=_num(cur.get("amount"), float, 0),
        open_=_num(pankou.get("open"), float, 0), high=_num(pankou.get("high"), float, 0),
        low=_num(pankou.get("low"), float, 0), prev_close=_num(pankou.get("preClose")),
        amplitude=_num(pankou.get("amplitudeRatio")), turnover=_num(pankou.get("turnoverRatio")),
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
    """按 symbols 取 A股/指数实时行情，多源逐级补缺口。

    只处理沪深标的（`.SH`/`.SZ`）；HK/US 忽略，交由各自 fetcher。指数靠 secid /
    sh-sz 前缀天然消歧（000001.SH 上证指数 vs 000001.SZ 平安银行 不串号）。

    Returns:
        canonical 形状的行情列表（`symbol` 唯一）。全源皆失败时返回已拿到的部分
        （可能为空）——调用方按「空=稍后重试」处理，与旧行为一致。
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

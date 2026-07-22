"""币安现货**交易**签名接口门面 —— 账户/下单/撤单/查单（需 API key）。

与行情门面 `crypto.py`（公开、无 key）分离：这里是**鉴权**端点，key/secret 走
`.env`（`BINANCE_API_KEY`/`BINANCE_API_SECRET`），`os.getenv` 直读，UI 永不碰。

## 出网合规

同 `crypto.py`：经 `channels.make_session(Channel.OVERSEAS)`，绕开国内快代理池。
签名端点手动 HMAC-SHA256 签 query（币安 spec），不引第三方 SDK（SDK 自管 session/代理，
会绕过本项目出网层）。

## symbol

传入项目内形态（`BTCUSDT.BN`），内部过 `to_binance_symbol()` 剥后缀。

## 安全边界

本模块只做「把签名请求发出去」。**是否允许下单、二次人工确认、风控**都在上层
（`agents/tools/crypto_tools.py` 的 confirm_gate + RiskManager），本层不判权限。
key 未配置时 `has_credentials()` 返回 False，上层据此拒绝交易类调用。
"""
import hashlib
import hmac
import os
import time
import urllib.parse
from functools import lru_cache
from typing import Any

from acquisition.channels import Channel, make_session
from common.market import to_binance_symbol

_BASE = os.getenv("BINANCE_TRADE_BASE", "https://api.binance.com").rstrip("/")
_RECV_WINDOW = int(os.getenv("BINANCE_RECV_WINDOW", "5000"))


def _creds() -> tuple[str, str]:
    return os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", "")


def has_credentials() -> bool:
    """key + secret 都配了才算有凭证（上层据此决定能否交易）。"""
    key, secret = _creds()
    return bool(key and secret)


def _signed_request(method: str, path: str, params: dict[str, Any] | None = None,
                    timeout: int = 15) -> Any:
    """发一个签名请求（账户/交易端点）。key 未配抛 RuntimeError（上层应先 has_credentials 挡）。"""
    key, secret = _creds()
    if not (key and secret):
        raise RuntimeError("BINANCE_API_KEY/SECRET 未配置，无法调用交易接口")
    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = _RECV_WINDOW
    query = urllib.parse.urlencode(p)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{_BASE}{path}?{query}&signature={sig}"
    session = make_session(Channel.OVERSEAS)
    resp = session.request(method, url, headers={"X-MBX-APIKEY": key}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _public_get(path: str, params: dict[str, Any] | None = None, timeout: int = 15) -> Any:
    session = make_session(Channel.OVERSEAS)
    resp = session.get(f"{_BASE}{path}", params=params or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


# ── 账户 / 交易 ─────────────────────────────────────────────────────────
def account() -> dict:
    """现货账户（含各币种 free/locked 余额）。签名。"""
    return _signed_request("GET", "/api/v3/account")


def place_order(symbol: str, side: str, quantity: float,
                price: float | None = None,
                client_order_id: str | None = None) -> dict:
    """下单。price=None → MARKET，否则 LIMIT(GTC)。side=BUY/SELL。返回币安原始回执。签名。

    Args:
        client_order_id: 幂等键，透传币安 `newClientOrderId`。**强烈建议传**：请求超时但币安
            已受理时，调用方可用 `query_order_by_client_id()` 回查真实状态，避免误判失败后重下
            造成同一笔成交两次。币安约束 `^[\\.A-Z\\:/a-z0-9_-]{1,36}$`，由 `safe_client_order_id()`
            清洗保证。
    """
    bn = to_binance_symbol(symbol)
    params: dict[str, Any] = {
        "symbol": bn, "side": side.upper(),
        "quantity": _fmt_num(quantity),
    }
    if client_order_id:
        params["newClientOrderId"] = client_order_id
    if price is None:
        params["type"] = "MARKET"
    else:
        params["type"] = "LIMIT"
        params["timeInForce"] = "GTC"
        params["price"] = _fmt_num(price)
    return _signed_request("POST", "/api/v3/order", params)


_CID_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:/_-")


def safe_client_order_id(raw: str) -> str:
    """把任意字符串清洗成币安可接受的 clientOrderId（`^[\\.A-Z\\:/a-z0-9_-]{1,36}$`）。

    非法字符换 `-`，超长截**尾部** 36 字符——我们的 ref 形如 `CPO-20260722102453-abc123`，
    尾部的随机 hex 才是区分度所在，截头会让同秒排出的两张单撞成同一个键。
    """
    cleaned = "".join(c if c in _CID_ALLOWED else "-" for c in raw)
    return cleaned[-36:] or "fin"


def query_order(symbol: str, order_id: str) -> dict:
    return _signed_request("GET", "/api/v3/order",
                           {"symbol": to_binance_symbol(symbol), "orderId": order_id})


def query_order_by_client_id(symbol: str, client_order_id: str) -> dict | None:
    """按 clientOrderId 回查订单；**不存在返回 None**（而非抛异常）。

    幂等恢复专用：下单请求超时/断连时，用它判断「币安到底受理了没有」。币安对查不到的单
    返回 400 + code -2013 (Order does not exist)，这里翻译成 None，其余异常照抛（网络还没恢复
    就不该假装「没下单」——那会导致重下）。
    """
    import requests
    try:
        return _signed_request("GET", "/api/v3/order",
                               {"symbol": to_binance_symbol(symbol),
                                "origClientOrderId": client_order_id})
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        if resp is not None and resp.status_code == 400:
            try:
                if (resp.json() or {}).get("code") == -2013:
                    return None      # 确认币安没有这张单
            except ValueError:
                pass
        raise


def cancel_order(symbol: str, order_id: str) -> dict:
    return _signed_request("DELETE", "/api/v3/order",
                           {"symbol": to_binance_symbol(symbol), "orderId": order_id})


def open_orders(symbol: str | None = None) -> list[dict]:
    params = {"symbol": to_binance_symbol(symbol)} if symbol else {}
    return _signed_request("GET", "/api/v3/openOrders", params)


def my_trades(symbol: str, from_id: int | None = None, limit: int = 1000) -> list[dict]:
    """某交易对的**账户成交明细**（持仓成本的唯一数据来源）。签名，权重 20。

    币安不提供任何「持仓成本」端点，成本只能靠本接口的逐笔成交回放重建
    （App 里的成本价也是这么算的）。

    ⚠️ **必须用 `from_id` 翻页拉全历史，不要用时间窗**：币安限制 `startTime`/`endTime`
    跨度不超过 24 小时，用时间窗拉几年历史要几百次请求还容易漏；`fromId` 没有这个限制，
    每页最多 1000 条，下一页传 `上页末条 id + 1`。

    Args:
        from_id: 从这个 tradeId 开始（含）。None = 拉**最近**的一批（首次全量应传 0）。

    Returns:
        逐笔成交，字段：`id`/`orderId`/`price`/`qty`/`quoteQty`/`commission`/
        `commissionAsset`/`isBuyer`/`isMaker`/`time`(ms UTC)。按 id 升序。
    """
    params: dict[str, Any] = {"symbol": to_binance_symbol(symbol), "limit": limit}
    if from_id is not None:
        params["fromId"] = from_id
    return _signed_request("GET", "/api/v3/myTrades", params) or []


def convert_trade_flow(start_ms: int, end_ms: int, limit: int = 1000) -> list[dict]:
    """币安「闪兑」成交历史。签名，权重高（UID 3000），**窗口最长 30 天**。

    闪兑**不进 `myTrades`** —— 用过闪兑换币的话，光看现货成交会漏掉那部分成本。
    `startTime`/`endTime` 都是必填，超过 30 天要自己分段。
    """
    d = _signed_request("GET", "/sapi/v1/convert/tradeFlow",
                        {"startTime": start_ms, "endTime": end_ms, "limit": limit})
    if isinstance(d, dict):
        return d.get("list", []) or []
    return d or []


def ticker_price(symbol: str) -> float:
    d = _public_get("/api/v3/ticker/price", {"symbol": to_binance_symbol(symbol)})
    return float(d.get("price", 0))


# ── 钱包 / 划转 / 理财（现货以外的资产 + 资金腾挪）────────────────────────
#
# 现货账户 `account()` 只含 Spot 钱包。Jason 的资金常在 **Funding（资金钱包）** 或
# **Simple Earn（理财活期/定期）**，必须单独查这些端点才看得到（这是「账户读不到钱」
# 的根因）。划转/申赎用于「买入时把闲置理财/资金腾到现货」「卖出后把闲置现货扫进理财」。
def funding_asset() -> list[dict]:
    """资金钱包各币余额（free/locked/freeze）。签名。POST（币安此端点用 POST）。"""
    return _signed_request("POST", "/sapi/v1/asset/get-funding-asset") or []


def earn_flexible_positions(asset: str | None = None) -> list[dict]:
    """Simple Earn **活期**理财持仓。返回 rows（[{asset,totalAmount,productId,...}]）。签名。"""
    params = {"asset": asset} if asset else None
    d = _signed_request("GET", "/sapi/v1/simple-earn/flexible/position", params)
    return (d or {}).get("rows", []) if isinstance(d, dict) else []


def earn_locked_positions(asset: str | None = None) -> list[dict]:
    """Simple Earn **定期**理财持仓（仅展示用，锁仓期内不可动）。返回 rows。签名。"""
    params = {"asset": asset} if asset else None
    d = _signed_request("GET", "/sapi/v1/simple-earn/locked/position", params)
    return (d or {}).get("rows", []) if isinstance(d, dict) else []


def universal_transfer(transfer_type: str, asset: str, amount: float) -> dict:
    """钱包间划转。`transfer_type`：`FUNDING_MAIN`(资金→现货)/`MAIN_FUNDING`(现货→资金)。签名。"""
    return _signed_request("POST", "/sapi/v1/asset/transfer",
                           {"type": transfer_type, "asset": asset, "amount": _fmt_num(amount)})


def earn_flexible_list(asset: str | None = None, size: int = 20) -> list[dict]:
    """可申购的**活期**理财产品列表（含 `latestAnnualPercentageRate`/`canPurchase`/`productId`）。签名。"""
    params: dict[str, Any] = {"size": size, "current": 1}
    if asset:
        params["asset"] = asset
    d = _signed_request("GET", "/sapi/v1/simple-earn/flexible/list", params)
    return (d or {}).get("rows", []) if isinstance(d, dict) else []


def earn_flexible_subscribe(product_id: str, amount: float) -> dict:
    """申购活期理财（把现货 USDT 转进理财吃收益）。签名。"""
    return _signed_request("POST", "/sapi/v1/simple-earn/flexible/subscribe",
                           {"productId": product_id, "amount": _fmt_num(amount)})


def earn_flexible_redeem(product_id: str, amount: float | None = None,
                         redeem_all: bool = False) -> dict:
    """赎回活期理财（把理财 USDT 赎回现货以便下单）。amount 与 redeem_all 二选一。签名。"""
    params: dict[str, Any] = {"productId": product_id}
    if redeem_all:
        params["redeemAll"] = "true"
    elif amount is not None:
        params["amount"] = _fmt_num(amount)
    return _signed_request("POST", "/sapi/v1/simple-earn/flexible/redeem", params)


# ── 交易对精度过滤器（LOT_SIZE / MIN_NOTIONAL）──────────────────────────
@lru_cache(maxsize=512)
def symbol_filters(symbol: str) -> dict:
    """某交易对的下单精度约束：{step_size, min_qty, min_notional, tick_size}。缓存。

    币安拒单最常见原因就是数量没对齐 LOT_SIZE 的 stepSize、或金额低于 MIN_NOTIONAL。
    """
    bn = to_binance_symbol(symbol)
    data = _public_get("/api/v3/exchangeInfo", {"symbol": bn})
    syms = (data or {}).get("symbols") or []
    out = {"step_size": None, "min_qty": None, "min_notional": None, "tick_size": None}
    if not syms:
        return out
    for f in syms[0].get("filters", []):
        t = f.get("filterType")
        if t == "LOT_SIZE":
            out["step_size"] = float(f.get("stepSize", 0)) or None
            out["min_qty"] = float(f.get("minQty", 0)) or None
        elif t in ("MIN_NOTIONAL", "NOTIONAL"):
            out["min_notional"] = float(f.get("minNotional", 0)) or None
        elif t == "PRICE_FILTER":
            out["tick_size"] = float(f.get("tickSize", 0)) or None
    return out


def round_step(quantity: float, step: float | None) -> float:
    """把数量向下取整到 stepSize 的整数倍（币安 LOT_SIZE 要求）。step 缺失原样返回。

    全程用 Decimal(str(...))：既避开浮点误差，也不假设 step 是 10 的幂 —— 非幂步长
    （如 0.005）用 log10 推小数位会把 floor 结果又抬回上一格甚至超余额被拒。
    """
    if not step or step <= 0:
        return quantity
    from decimal import ROUND_DOWN, Decimal
    q, s = Decimal(str(quantity)), Decimal(str(step))
    val = (q // s) * s   # 向下取整到 step 的整数倍（精确）
    return float(val.quantize(s, rounding=ROUND_DOWN))


def round_price(price: float, tick: float | None) -> float:
    """把价格对齐到 tickSize 的整数倍（币安 PRICE_FILTER 要求）。tick 缺失原样返回。

    取最近的 tick 整数格（四舍五入），限价单不对齐 tick 会被币安 -1013 拒单。
    """
    if not tick or tick <= 0:
        return price
    from decimal import ROUND_HALF_UP, Decimal
    p, t = Decimal(str(price)), Decimal(str(tick))
    n = (p / t).quantize(Decimal(1), rounding=ROUND_HALF_UP)   # 最近整数格
    return float((n * t).quantize(t))


def _fmt_num(x: float) -> str:
    """数量/价格字符串化，去掉多余尾零（币安要求纯数字串，不接受科学计数）。"""
    return format(x, "f").rstrip("0").rstrip(".") or "0"

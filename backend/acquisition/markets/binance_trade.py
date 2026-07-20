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
                price: float | None = None) -> dict:
    """下单。price=None → MARKET，否则 LIMIT(GTC)。side=BUY/SELL。返回币安原始回执。签名。"""
    bn = to_binance_symbol(symbol)
    params: dict[str, Any] = {
        "symbol": bn, "side": side.upper(),
        "quantity": _fmt_num(quantity),
    }
    if price is None:
        params["type"] = "MARKET"
    else:
        params["type"] = "LIMIT"
        params["timeInForce"] = "GTC"
        params["price"] = _fmt_num(price)
    return _signed_request("POST", "/api/v3/order", params)


def query_order(symbol: str, order_id: str) -> dict:
    return _signed_request("GET", "/api/v3/order",
                           {"symbol": to_binance_symbol(symbol), "orderId": order_id})


def cancel_order(symbol: str, order_id: str) -> dict:
    return _signed_request("DELETE", "/api/v3/order",
                           {"symbol": to_binance_symbol(symbol), "orderId": order_id})


def open_orders(symbol: str | None = None) -> list[dict]:
    params = {"symbol": to_binance_symbol(symbol)} if symbol else {}
    return _signed_request("GET", "/api/v3/openOrders", params)


def ticker_price(symbol: str) -> float:
    d = _public_get("/api/v3/ticker/price", {"symbol": to_binance_symbol(symbol)})
    return float(d.get("price", 0))


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

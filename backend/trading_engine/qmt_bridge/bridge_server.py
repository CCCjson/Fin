"""
QMT Bridge Server — 运行在 CrossOver Wine Python 中

轻量 HTTP 服务，包装 xtquant 调用，供 Mac 原生 Python 通过 HTTP 访问。
监听 127.0.0.1:5100。

启动方式:
    wine python bridge_server.py
    或通过 start_bridge.sh 脚本启动

依赖:
    - xtquant (miniQMT 自带)
    - Flask (pip install flask) 或使用内置 http.server 备选模式
"""
import json
import sys
import os
import traceback
from datetime import datetime

# ────────────────────────────────────────────
# 尝试导入 Flask，失败则回退到 stdlib
# ────────────────────────────────────────────
try:
    from flask import Flask, request, jsonify
    USE_FLASK = True
except ImportError:
    USE_FLASK = False

# ────────────────────────────────────────────
# xtquant 导入
# ────────────────────────────────────────────
try:
    from xtquant import xttrader, xtdata
    from xtquant.xttrader import XtQuantTrader
    from xtquant.xttype import StockAccount
    XTQUANT_AVAILABLE = True
except ImportError:
    XTQUANT_AVAILABLE = False
    print("[WARN] xtquant 未找到，Bridge 将以模拟模式运行（仅用于测试）")

# ────────────────────────────────────────────
# 全局状态
# ────────────────────────────────────────────
trader = None          # XtQuantTrader 实例
account = None         # StockAccount 实例
qmt_connected = False  # 是否已连接

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 5100

# xtquant order_status 整数 → 字符串
ORDER_STATUS_MAP = {
    48: "SUBMITTED",      # 已报
    49: "PARTIAL_FILLED",  # 部分成交
    50: "FILLED",          # 已成交
    51: "PARTIAL_CANCELLED",  # 部分撤单
    52: "CANCELLED",       # 已撤单
    53: "REJECTED",        # 已拒绝
    54: "PENDING",         # 待报
    55: "FAILED",          # 废单
    56: "UNKNOWN",         # 未知
}


def _normalize_symbol(symbol: str) -> str:
    """
    股票代码自动补后缀

    600519 → 600519.SH  (沪市)
    000858 → 000858.SZ  (深市)
    300xxx → 300xxx.SZ  (创业板)
    688xxx → 688xxx.SH  (科创板)
    """
    symbol = symbol.strip()
    if "." in symbol:
        return symbol

    code = symbol.zfill(6)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


def _strip_suffix(symbol: str) -> str:
    """去掉后缀: 600519.SH → 600519"""
    return symbol.split(".")[0] if "." in symbol else symbol


# ============================================================
# 核心业务逻辑（Flask 和 stdlib 共用）
# ============================================================

def handle_health():
    """GET /health"""
    return {
        "status": "ok",
        "qmt_connected": qmt_connected,
        "account_id": account.account_id if account else None,
        "xtquant_available": XTQUANT_AVAILABLE,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def handle_connect(data: dict):
    """POST /connect"""
    global trader, account, qmt_connected

    if not XTQUANT_AVAILABLE:
        return {"success": False, "error": "xtquant 不可用，请确认 miniQMT 已安装"}

    account_id = data.get("account_id", "")
    mini_path = data.get("mini_path", "")

    if not account_id:
        return {"success": False, "error": "缺少 account_id"}
    if not mini_path:
        return {"success": False, "error": "缺少 mini_path (miniQMT userdata 路径)"}

    try:
        # 创建 session_id（用进程 PID）
        session_id = int(os.getpid())
        trader = XtQuantTrader(mini_path, session_id)
        trader.start()

        account = StockAccount(account_id)
        connect_result = trader.connect()
        if connect_result != 0:
            return {"success": False, "error": f"连接失败，错误码: {connect_result}"}

        subscribe_result = trader.subscribe(account)
        if subscribe_result != 0:
            return {"success": False, "error": f"订阅账户失败，错误码: {subscribe_result}"}

        qmt_connected = True
        return {"success": True, "message": f"已连接，账户: {account_id}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_account():
    """GET /account"""
    global trader, account
    if not qmt_connected:
        return {"success": False, "error": "QMT 未连接"}

    try:
        asset = trader.query_stock_asset(account)
        if asset is None:
            return {"success": False, "error": "查询资金失败"}

        return {
            "success": True,
            "data": {
                "cash": asset.cash,
                "market_value": asset.market_value,
                "total_value": asset.total_asset,
                "frozen": asset.frozen_cash,
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_positions():
    """GET /positions"""
    global trader, account
    if not qmt_connected:
        return {"success": False, "error": "QMT 未连接"}

    try:
        positions = trader.query_stock_positions(account)
        data = []
        for pos in positions:
            data.append({
                "symbol": _strip_suffix(pos.stock_code),
                "quantity": pos.volume,
                "avg_cost": pos.avg_price,
                "current_price": pos.market_value / pos.volume if pos.volume > 0 else 0,
                "market_value": pos.market_value,
                "unrealized_pnl": pos.market_value - pos.avg_price * pos.volume if pos.volume > 0 else 0,
                "available": pos.can_use_volume,
            })
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_order(data: dict):
    """POST /order"""
    global trader, account
    if not qmt_connected:
        return {"success": False, "error": "QMT 未连接"}

    symbol = data.get("symbol", "")
    action = data.get("action", "").upper()
    quantity = int(data.get("quantity", 0))
    price = data.get("price")

    if not symbol or not action or quantity <= 0:
        return {"success": False, "error": "参数不完整: 需要 symbol, action, quantity"}

    xt_symbol = _normalize_symbol(symbol)

    # xtquant 的买卖类型
    # xtconstant.STOCK_BUY = 23, STOCK_SELL = 24
    try:
        from xtquant import xtconstant
        if action == "BUY":
            order_type = xtconstant.STOCK_BUY
        elif action == "SELL":
            order_type = xtconstant.STOCK_SELL
        else:
            return {"success": False, "error": f"不支持的操作: {action}"}

        # 限价单 vs 市价单
        if price is not None and price > 0:
            price_type = xtconstant.FIX_PRICE  # 限价
            order_price = float(price)
        else:
            price_type = xtconstant.LATEST_PRICE  # 最新价
            order_price = 0

        order_id = trader.order_stock(
            account, xt_symbol, order_type, quantity, price_type, order_price
        )

        if order_id and order_id > 0:
            return {"success": True, "order_id": order_id}
        else:
            return {"success": False, "error": f"下单失败，返回值: {order_id}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_cancel(data: dict):
    """POST /cancel"""
    global trader, account
    if not qmt_connected:
        return {"success": False, "error": "QMT 未连接"}

    order_id = data.get("order_id")
    if not order_id:
        return {"success": False, "error": "缺少 order_id"}

    try:
        result = trader.cancel_order_stock(account, int(order_id))
        if result == 0:
            return {"success": True}
        else:
            return {"success": False, "error": f"撤单失败，错误码: {result}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_orders():
    """GET /orders"""
    global trader, account
    if not qmt_connected:
        return {"success": False, "error": "QMT 未连接"}

    try:
        orders = trader.query_stock_orders(account)
        data = []
        for o in orders:
            status_str = ORDER_STATUS_MAP.get(o.order_status, "UNKNOWN")
            data.append({
                "order_id": str(o.order_id),
                "symbol": _strip_suffix(o.stock_code),
                "action": "BUY" if o.order_type == 23 else "SELL",
                "quantity": o.order_volume,
                "price": o.price,
                "filled_quantity": o.traded_volume,
                "filled_price": o.traded_price,
                "status": status_str,
            })
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


def handle_price(symbol: str):
    """GET /price/<symbol>"""
    if not XTQUANT_AVAILABLE:
        return {"success": False, "error": "xtquant 不可用"}

    try:
        xt_symbol = _normalize_symbol(symbol)
        tick = xtdata.get_full_tick([xt_symbol])
        if tick and xt_symbol in tick:
            t = tick[xt_symbol]
            price = t.get("lastPrice", 0)
            return {"success": True, "price": price, "symbol": symbol}
        return {"success": False, "error": f"无法获取 {symbol} 的价格"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# Flask 模式
# ============================================================
if USE_FLASK:
    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def flask_health():
        return jsonify(handle_health())

    @app.route("/connect", methods=["POST"])
    def flask_connect():
        return jsonify(handle_connect(request.get_json(force=True, silent=True) or {}))

    @app.route("/account", methods=["GET"])
    def flask_account():
        return jsonify(handle_account())

    @app.route("/positions", methods=["GET"])
    def flask_positions():
        return jsonify(handle_positions())

    @app.route("/order", methods=["POST"])
    def flask_order():
        return jsonify(handle_order(request.get_json(force=True, silent=True) or {}))

    @app.route("/cancel", methods=["POST"])
    def flask_cancel():
        return jsonify(handle_cancel(request.get_json(force=True, silent=True) or {}))

    @app.route("/orders", methods=["GET"])
    def flask_orders():
        return jsonify(handle_orders())

    @app.route("/price/<symbol>", methods=["GET"])
    def flask_price(symbol):
        return jsonify(handle_price(symbol))


# ============================================================
# stdlib http.server 备选模式（无需 Flask）
# ============================================================
else:
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class BridgeHandler(BaseHTTPRequestHandler):
        """简易 HTTP 请求处理器"""

        def _send_json(self, data: dict, status: int = 200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return {}

        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                if path == "/health":
                    self._send_json(handle_health())
                elif path == "/account":
                    self._send_json(handle_account())
                elif path == "/positions":
                    self._send_json(handle_positions())
                elif path == "/orders":
                    self._send_json(handle_orders())
                elif path.startswith("/price/"):
                    symbol = path.split("/price/")[1]
                    self._send_json(handle_price(symbol))
                else:
                    self._send_json({"error": "Not found"}, 404)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        def do_POST(self):
            path = self.path.split("?")[0]
            data = self._read_body()
            try:
                if path == "/connect":
                    self._send_json(handle_connect(data))
                elif path == "/order":
                    self._send_json(handle_order(data))
                elif path == "/cancel":
                    self._send_json(handle_cancel(data))
                else:
                    self._send_json({"error": "Not found"}, 404)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        def log_message(self, format, *args):
            """简化日志输出"""
            print(f"[Bridge] {args[0]}")


# ============================================================
# 启动入口
# ============================================================
if __name__ == "__main__":
    print(f"╔══════════════════════════════════════════╗")
    print(f"║   QMT Bridge Server                      ║")
    print(f"║   监听: {BRIDGE_HOST}:{BRIDGE_PORT}              ║")
    print(f"║   模式: {'Flask' if USE_FLASK else 'stdlib http.server'}                    ║")
    print(f"║   xtquant: {'可用' if XTQUANT_AVAILABLE else '不可用 (模拟模式)'}               ║")
    print(f"╚══════════════════════════════════════════╝")

    if USE_FLASK:
        app.run(host=BRIDGE_HOST, port=BRIDGE_PORT, debug=False)
    else:
        server = HTTPServer((BRIDGE_HOST, BRIDGE_PORT), BridgeHandler)
        print(f"[Bridge] 已启动，按 Ctrl+C 停止")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[Bridge] 已停止")
            server.server_close()

"""
持仓对账服务 —— 比对"系统持仓（ManualTrade 回放）"与"券商真实持仓（手录/CSV）"，
标记差异，并可通过插入调整交易（source_type='reconcile'）把系统拉齐到现实。

系统没有独立持仓表，持仓全靠 ManualTrade 回放（见 portfolio/calculator.py），
因此纠正持仓的唯一方式就是插入一笔调整交易。
"""
import json
import re
from datetime import date
from typing import Any, Dict, List, Optional

from loguru import logger

from common.market import A_SHARE
from common.market_time import market_today
from data_engine.storage.database import get_session
from data_engine.storage.models import ManualTrade, PositionReconciliation, StockInfo
from portfolio.calculator import PortfolioCalculator

# 数量视为一致的容差（股）；成本视为一致的容差（元）
_QTY_TOL = 0
_COST_TOL = 0.01


def _norm_symbol(raw: str) -> str:
    """规范化股票代码：从 '600519.SH' / 'SH600519' / '600519' 提取 6 位纯代码（用于匹配）。"""
    if raw is None:
        return ""
    s = str(raw).strip().upper()
    m = re.search(r"\d{6}", s)
    return m.group(0) if m else s


def _full_symbol(code: str) -> str:
    """6 位纯码 → 带交易所后缀（系统内 ManualTrade.symbol 用带后缀格式）。
    6xxxxx→.SH；0/3xxxxx→.SZ；4/8xxxxx→.BJ。"""
    c = _norm_symbol(code)
    if len(c) != 6:
        return code
    if c[0] == "6":
        return f"{c}.SH"
    if c[0] in ("0", "3"):
        return f"{c}.SZ"
    if c[0] in ("4", "8"):
        return f"{c}.BJ"
    return f"{c}.SH"


def parse_holdings_text(text: str) -> List[Dict[str, Any]]:
    """把手录/券商导出的文本（CSV/TSV/粘贴）解析为 [{symbol, quantity, avg_cost}]。

    兼容：①带表头（证券代码/持仓数量/成本价 等中文列，或 symbol/quantity/avg_cost）；
         ②无表头时按 "代码, 数量, 成本" 位置解析。分隔符支持逗号/中文逗号/制表/空格。
    """
    holdings: List[Dict[str, Any]] = []
    if not text or not text.strip():
        return holdings

    lines = [ln for ln in text.replace("，", ",").splitlines() if ln.strip()]
    if not lines:
        return holdings

    def split_cols(line: str) -> List[str]:
        if "," in line:
            return [c.strip() for c in line.split(",")]
        if "\t" in line:
            return [c.strip() for c in line.split("\t")]
        return [c.strip() for c in line.split()]

    # 关键词 → 字段（注意 "证券" 不能进 sym_kw，否则会误命中"证券名称"列）
    sym_kw = ("代码", "symbol", "code")
    qty_kw = ("数量", "余额", "股数", "quantity", "qty", "shares", "持仓")
    cost_kw = ("成本", "cost", "均价", "avg")

    header = split_cols(lines[0])
    header_lower = [h.lower() for h in header]
    has_header = any(any(k in h for k in (sym_kw + qty_kw + cost_kw)) for h in header_lower)

    idx_sym, idx_qty, idx_cost = 0, 1, 2
    start = 0
    if has_header:
        start = 1
        for i, h in enumerate(header_lower):
            if any(k in h for k in sym_kw):
                idx_sym = i
            elif any(k in h for k in qty_kw):
                idx_qty = i
            elif any(k in h for k in cost_kw):
                idx_cost = i

    for line in lines[start:]:
        cols = split_cols(line)
        if len(cols) <= max(idx_sym, idx_qty):
            continue
        symbol = _norm_symbol(cols[idx_sym])
        if not symbol or not re.match(r"\d{6}", symbol):
            continue
        try:
            qty = int(float(re.sub(r"[^\d.\-]", "", cols[idx_qty])))
        except (ValueError, IndexError):
            continue
        avg_cost = None
        if idx_cost < len(cols):
            try:
                avg_cost = float(re.sub(r"[^\d.\-]", "", cols[idx_cost]))
            except ValueError:
                avg_cost = None
        holdings.append({"symbol": symbol, "quantity": qty, "avg_cost": avg_cost})
    return holdings


class ReconciliationService:
    """持仓对账。"""

    def __init__(self):
        self._calc = PortfolioCalculator()

    # ---------- 差异比对 ----------

    def _diff(self, actual_holdings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """比对系统持仓 vs 真实持仓，返回逐股差异 + 汇总。不写库。"""
        # 系统持仓按 6 位纯码归并（ManualTrade.symbol 带 .SH/.SZ 后缀），保留完整代码用于插调整交易
        system = {}
        for p in self._calc.get_current_positions():
            system[_norm_symbol(p["symbol"])] = {**p, "full_symbol": p["symbol"]}
        actual = {}
        for h in actual_holdings:
            sym = _norm_symbol(h.get("symbol", ""))
            if not sym:
                continue
            actual[sym] = {
                "quantity": int(h.get("quantity") or 0),
                "avg_cost": h.get("avg_cost"),
            }

        rows: List[Dict[str, Any]] = []
        counts = {"match": 0, "qty_mismatch": 0, "cost_mismatch": 0,
                  "system_only": 0, "broker_only": 0}

        for sym in sorted(set(system) | set(actual)):
            sp = system.get(sym)
            ap = actual.get(sym)
            s_qty = sp["quantity"] if sp else 0
            a_qty = ap["quantity"] if ap else 0
            s_avg = sp["avg_cost"] if sp else None
            a_avg = ap["avg_cost"] if ap else None
            name = sp["name"] if sp else None

            if s_qty > 0 and a_qty == 0:
                kind = "system_only"       # 系统有、券商没有（多算了）
            elif s_qty == 0 and a_qty > 0:
                kind = "broker_only"       # 券商有、系统没有（漏记了）
            elif abs(s_qty - a_qty) > _QTY_TOL:
                kind = "qty_mismatch"      # 数量不符
            elif a_avg is not None and s_avg is not None and abs(s_avg - a_avg) > _COST_TOL:
                kind = "cost_mismatch"     # 数量一致、成本不符
            else:
                kind = "match"
            counts[kind] += 1

            rows.append({
                "symbol": sym, "name": name,
                # 插调整交易用带后缀的完整代码：系统有则沿用，否则按纯码推断交易所
                "full_symbol": sp["full_symbol"] if sp else _full_symbol(sym),
                "system_qty": s_qty, "actual_qty": a_qty, "qty_delta": a_qty - s_qty,
                "system_avg": round(s_avg, 4) if s_avg is not None else None,
                "actual_avg": round(a_avg, 4) if a_avg is not None else None,
                "kind": kind,
                # 建议的纠正（把系统数量拉到与真实一致）
                "suggested": self._suggest(kind, s_qty, a_qty, s_avg, a_avg),
            })

        return {
            "summary": counts,
            "rows": rows,
            "has_discrepancy": any(v for k, v in counts.items() if k != "match"),
        }

    @staticmethod
    def _suggest(kind, s_qty, a_qty, s_avg, a_avg) -> Optional[Dict[str, Any]]:
        """给出把系统持仓拉齐到真实持仓的调整交易建议；成本类偏差不自动纠正。"""
        if kind in ("match", "cost_mismatch"):
            return None
        delta = a_qty - s_qty
        if delta > 0:   # 券商更多 → 补买
            price = a_avg if (a_avg and a_avg > 0) else (s_avg or 0)
            return {"side": "BUY", "quantity": delta, "price": round(price or 0, 4)}
        else:           # 系统更多 → 卖掉多算的（按系统成本出，避免虚增盈亏）
            price = s_avg if (s_avg and s_avg > 0) else (a_avg or 0)
            return {"side": "SELL", "quantity": -delta, "price": round(price or 0, 4)}

    def preview(self, actual_holdings: List[Dict[str, Any]]) -> Dict[str, Any]:
        """只算差异，不写库。"""
        return self._diff(actual_holdings)

    # ---------- 应用纠正 ----------

    def apply(
        self,
        actual_holdings: List[Dict[str, Any]],
        symbols_to_fix: Optional[List[str]] = None,
        note: Optional[str] = None,
        reconcile_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """对选中的差异插入调整交易（source_type='reconcile'），并记录一条对账记录。

        Args:
            actual_holdings: 真实持仓 [{symbol, quantity, avg_cost}]
            symbols_to_fix: 要纠正的代码；None=纠正所有有建议的差异
            note: 备注
        """
        diff = self._diff(actual_holdings)
        reconcile_date = reconcile_date or market_today(A_SHARE)
        fix_set = {_norm_symbol(s) for s in symbols_to_fix} if symbols_to_fix else None

        adjustments: List[Dict[str, Any]] = []
        session = get_session()
        try:
            for row in diff["rows"]:
                sug = row["suggested"]
                if not sug:
                    continue
                if fix_set is not None and row["symbol"] not in fix_set:
                    continue

                full_symbol = row["full_symbol"]
                name = row["name"]
                if not name:
                    stock = session.query(StockInfo).filter(
                        StockInfo.symbol == full_symbol).first()
                    name = stock.name if stock else None

                qty = sug["quantity"]
                price = sug["price"]
                trade = ManualTrade(
                    symbol=full_symbol,
                    name=name,
                    side=sug["side"],
                    price=price,
                    quantity=qty,
                    amount=price * qty,
                    commission=0.0,
                    trade_date=reconcile_date,
                    note=f"[对账调整] 系统{row['system_qty']} → 券商{row['actual_qty']}股"
                         + (f"；{note}" if note else ""),
                    source_type="reconcile",
                )
                session.add(trade)
                session.flush()  # 拿到 trade.id
                adjustments.append({
                    "symbol": row["symbol"], "side": sug["side"],
                    "quantity": qty, "price": price, "manual_trade_id": trade.id,
                })

            rec = PositionReconciliation(
                reconcile_date=reconcile_date,
                source="manual",
                status="applied",
                summary=json.dumps(diff["summary"], ensure_ascii=False),
                details=json.dumps(diff["rows"], ensure_ascii=False, default=str),
                adjustments=json.dumps(adjustments, ensure_ascii=False),
                note=note,
            )
            session.add(rec)
            session.commit()
            session.refresh(rec)
            logger.info(f"持仓对账应用: {len(adjustments)} 笔调整交易, reconcile_id={rec.id}")

            return {
                "reconcile_id": rec.id,
                "adjustments": adjustments,
                "summary": diff["summary"],
                "applied_count": len(adjustments),
            }
        except Exception as e:
            session.rollback()
            logger.error(f"持仓对账应用失败: {e}")
            raise
        finally:
            session.close()

    # ---------- 历史 ----------

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        session = get_session()
        try:
            recs = (session.query(PositionReconciliation)
                    .order_by(PositionReconciliation.created_at.desc())
                    .limit(limit).all())
            out = []
            for r in recs:
                out.append({
                    "id": r.id,
                    "reconcile_date": r.reconcile_date.isoformat() if r.reconcile_date else None,
                    "source": r.source,
                    "status": r.status,
                    "summary": json.loads(r.summary) if r.summary else {},
                    "adjustments": json.loads(r.adjustments) if r.adjustments else [],
                    "note": r.note,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            return out
        finally:
            session.close()

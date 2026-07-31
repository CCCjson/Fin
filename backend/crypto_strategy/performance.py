"""策略战绩 —— 「我那条 BTC 策略跑得怎么样」的计算内核（S1）。

纯计算：不出网、不碰 LLM、不写库。只读 `crypto_strategy_runs` / `crypto_trades` /
`crypto_fills` / `crypto_strategies`。

# 三个必须先讲清楚的口径

### 1. ⛔ `pnl_realized_today` 不是这条策略的盈亏

`crypto_strategy_runs.pnl_realized_today` 填的是 `cost_basis.realized_on()` ——
**遍历所有 symbol = 整个账户**当日已实现盈亏。喂账户级熔断是对的，当策略收益是错的：
卫冕者下的单、Jason 在 MoneyBill 下的单、待确认单成交，全混在同一个币安账户里。
**看到这个字段名别顺手拿来用**，这是本模块最容易踩的坑。

### 2. live 的归因钥匙是 `CryptoTrade.source_ref`，不是 `executed_order_ids`

`crypto_strategy_runs.executed_order_ids` 存的是待确认单的 `CPO-…` 引用
（**不是币安 orderId**，模型里那句注释一度是假的），而 `CryptoPendingOrder` 的 FILLED
行 24 小时后会被 `pending.cleanup()` 删掉 —— 靠它 join 只能查回最近一天。
所以归因当场写进成交台账（`source_kind='strategy'` + `source_ref=<策略号>`，S1）。

拿到 `order_id` 之后，**盈亏走 `cost_basis.replay(order_ids=…)` 而不是直接算
`crypto_trades`**：那张台账的 `commission` 丢了 `commissionAsset`（买 BTC 扣的
0.00001 BTC 会被当成 0.00001 美元），`crypto_fills` 才是费用的真源。

### 3. 🔴 paper 和 live 的盈亏**不可直接比大小**

paper 不写 `CryptoTrade`（这个隔离是刻意的，别改），它的「成交」是 `decision_detail`
里的 `paper_would` —— **理想撮合：按决策价必成、零冲击、零排队**。本模块只负责算出来
并把 `basis` 标清楚，**比较规则留给 S3**（`00-PLAN §3` 裁决 9 的坑 1）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from common.market import CRYPTO
from common.market_time import market_day_of, utc_iso, utc_now
from common.trade_source import STRATEGY as TRADE_STRATEGY
from common.trade_source import UNKNOWN as TRADE_UNKNOWN

# 没开单的原因 → 人话。key 是 `engine._decide_symbol` 落进 decision_detail 的 status。
_NO_ORDER_LABELS = {
    "no_signal": "条件没触发",
    "skipped": "跳过（数据缺失/仓位换算为 0）",
    # ⚠️ 语义上它不是「没开单的原因」，是「同向单还挂着没结」—— 但没有 label 的话
    # verdict 会把 raw key 原样念给 Jason（「1 次是「skipped_dup」」）。
    "skipped_dup": "已有同向待确认单，未重复排单",
    "blocked_cost": "往返成本吃光了净边际",
    "blocked_risk": "风控没过",
    "blocked_guardrail": "护栏拦下（单笔上限/敞口/白名单）",
    "ready": "已产单",
}
# 只有这个 status 意味着「本可以开单」。
_READY = "ready"

_DEFAULT_TAKER_FEE = 0.001      # paper 记账用的兜底费率，与 DSL cost_model 同量纲


# ──────────────────────── 读取 ────────────────────────

def _strategy_row(strategy_id: str) -> Any:
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy
    session = get_session()
    try:
        return session.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == strategy_id).first()
    finally:
        session.close()


def _runs(strategy_id: str, since: datetime | None, until: datetime | None) -> list[dict]:
    """按时间正序取 run 行（转成 dict，出了 session 也能用）。

    ⚠️ `started_at` 是 **naive UTC**（模型文件头有声明）。窗口边界必须也用 naive UTC
    构造，⛔ 别拿 `date.today()` 去比 —— 那会错开 8 小时。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategyRun
    session = get_session()
    try:
        q = session.query(CryptoStrategyRun).filter(
            CryptoStrategyRun.strategy_id == strategy_id)
        if since:
            q = q.filter(CryptoStrategyRun.started_at >= since)
        if until:
            q = q.filter(CryptoStrategyRun.started_at < until)
        rows = q.order_by(CryptoStrategyRun.started_at.asc()).all()
        return [{
            "id": r.id, "status": r.status, "mode": r.mode,
            "symbols_evaluated": r.symbols_evaluated or 0,
            "orders_placed": r.orders_placed or 0,
            "detail": _loads(r.decision_detail),
            "started_at": r.started_at, "error_message": r.error_message,
        } for r in rows]
    finally:
        session.close()


def _loads(s: str | None) -> dict:
    if not s:
        return {}
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def _live_order_ids(strategy_id: str, since: datetime | None,
                    until: datetime | None) -> tuple[set[str], int]:
    """这条策略的成交对应哪些币安 orderId + 该窗口内**来源不明**的成交笔数。

    🔴 **`since` 刻意只用来数「来源不明」，不用来筛 order_ids。**
    成本基础天然需要**全历史**：买在 40 天前、卖在昨天，若按 30 天窗口筛掉买入腿，
    回放里就只剩一条卖出 → `costed = min(sold, 0) = 0` → 不记平仓 →
    **盈亏静默变成 0**，而 `days=30` 正是工具默认值、crypto 持仓跨月完全正常。
    窗口是**展示口径**，应该切在平仓日（`closed[].date`）上，不是切在成本累积上。

    `unknown` 计数是诚实度指标：「有 N 笔成交我不知道该算给谁」比悄悄少算强
    （存量行、以及 S1 之前落的成交都是 `unknown`）。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoTrade
    session = get_session()
    try:
        q = session.query(CryptoTrade).filter(
            CryptoTrade.source_kind == TRADE_STRATEGY,
            CryptoTrade.source_ref == strategy_id,
            CryptoTrade.order_id.isnot(None))
        if until:
            q = q.filter(CryptoTrade.trade_date <= market_day_of(until, CRYPTO))
        mine = {str(oid) for (oid,) in q.with_entities(CryptoTrade.order_id).all()}

        uq = session.query(CryptoTrade).filter(
            (CryptoTrade.source_kind == TRADE_UNKNOWN) | (CryptoTrade.source_kind.is_(None)))
        if since:
            uq = uq.filter(CryptoTrade.trade_date >= market_day_of(since, CRYPTO))
        if until:
            uq = uq.filter(CryptoTrade.trade_date <= market_day_of(until, CRYPTO))
        return mine, uq.count()
    finally:
        session.close()


# ──────────────────────── paper：理想撮合回放 ────────────────────────

def _live_block(strategy_id: str, since: datetime | None,
                until: datetime | None) -> dict[str, Any] | None:
    """真金白银那一半。没有可算的成交 → None。"""
    order_ids, unknown = _live_order_ids(strategy_id, since, until)
    coverage = {"attributed_trades": len(order_ids), "unknown_source_trades": unknown}
    if not order_ids:
        return None

    from crypto_intel_engine import cost_basis as cb
    try:
        state = cb.replay(order_ids=order_ids)
    except Exception as e:  # noqa: BLE001 — 战绩算不出来不该掀翻调用方
        logger.warning(f"策略 {strategy_id} live 盈亏回放失败: {e}")
        return {"basis": "none", "reason": f"成交明细回放失败：{e}", "coverage": coverage}

    fills = sum(st["fills"] for st in state.values())
    if not fills:
        # 🔴 **「算不出来」绝不能报成「赚了 0」。** `crypto_fills` 靠 `sync_held_fills`
        # 定时同步，成交刚落台账、明细还没拉下来时 `replay()` 返回空 —— 此时宣布
        # `realized_pnl=0` 与「真的平进平出」**完全无法区分**。
        # 项目里另外两个消费方（engine._today_fees / execution.get_recent_crypto_closed_pnls）
        # 都先问 `has_any_fills()` 再决定口径，这里是第三个，别漏。
        return {"basis": "none", "coverage": coverage,
                "reason": (f"这条策略名下有 {len(order_ids)} 笔成交，但币安成交明细"
                           f"（crypto_fills）里一笔都没匹配上 —— 多半是明细还没同步，"
                           f"不是「没赚钱」。跑一次成交明细同步再看。")}

    lo = market_day_of(since, CRYPTO).isoformat() if since else None
    hi = market_day_of(until, CRYPTO).isoformat() if until else None
    closed = [c for st in state.values() for c in st["closed"]
              if (lo is None or c["date"] >= lo) and (hi is None or c["date"] <= hi)]
    return {
        "basis": "live_fills",
        "realized_pnl": round(sum(c["pnl"] for c in closed), 4),
        "closed_legs": len(closed),
        # 逐笔平仓盈亏 —— 提案后验的**胜率**要数正负（S4）。只给数量的话，
        # 「赚 1000 亏 1」和「赚 1 亏 1000」在下游看起来一样。
        "closed_leg_pnls": [round(c["pnl"], 6) for c in closed],
        # ⚠️ 这是**策略子账本**的虚拟持仓，不是账户实际持仓：策略买了 1 BTC、Jason 手动
        # 卖掉的话，这里仍显示持有 1 BTC。真实持仓看币安账户。
        "book_positions": {s: {"quantity": round(st["quantity"], 8),
                               "avg_cost": round(st["avg_cost"], 6)}
                           for s, st in state.items() if st["quantity"] > 0},
        "has_uncosted_sell": any(st.get("has_uncosted_sell") for st in state.values()),
        "coverage": {**coverage, "fills_matched": fills},
        "note": ("真实成交（费用按 commissionAsset 正确折算）。成本从全历史累计，"
                 "窗口只切平仓日；book_positions 是策略子账本不是账户持仓。"),
    }


def _paper_legs(runs: list[dict]) -> list[dict]:
    """从 `decision_detail` 里捞出 paper 模式「本应成交」的腿。

    `engine._stage_or_log` 在 paper 下只写 `paper_would: "BUY 0.001 @ 65000"` 和
    `staged: False`，不排单不成交。所以这里认的是**决策本身**，撮合是理想化的。
    """
    legs: list[dict] = []
    for r in runs:
        if (r.get("mode") or "") != "paper":
            continue
        for d in (r.get("detail") or {}).get("decisions") or []:
            if d.get("status") != _READY or not d.get("action"):
                continue
            qty, price = d.get("qty"), d.get("price")
            if not qty or not price or qty <= 0 or price <= 0:
                continue
            legs.append({"symbol": d.get("symbol"), "side": d["action"],
                         "qty": float(qty), "price": float(price),
                         "at": r.get("started_at")})
    return legs


def _taker_fee(row: Any) -> float:
    """策略 DSL 里的吃单费率；读不到就用兜底值（**不静默当 0**）。

    paper 若按零手续费记账，会系统性高估它 —— 而 paper 本来就已经因为理想撮合被高估了，
    再免掉手续费就是在给挑战者发奖状。
    """
    try:
        cm = json.loads(getattr(row, "cost_model", None) or "{}")
        v = float(cm.get("taker_fee_pct") or 0)
        return v if v > 0 else _DEFAULT_TAKER_FEE
    except (TypeError, ValueError):
        return _DEFAULT_TAKER_FEE


def _pair(legs: list[dict], fee_pct: float, *,
          since: datetime | None = None) -> dict[str, Any]:
    """加权平均成本法配对，**与 `cost_basis.replay()` 同口径**（含「卖多于已知买入」的处理）。

    这里没有复用 `replay()` 本体，因为那个函数绑死在 `crypto_fills` 的行形状上；
    但两处的**规则必须一致**，否则 paper 和 live 的数字连口径都不同，S3 更没法比。

    Args:
        since: 传了就**只统计这之后的平仓**，更早的腿仅用于累积成本 ——
            否则窗口外的买入腿会被切掉，收益系统性低估（见 `strategy_pnl` 的注释）。
    """
    state: dict[str, dict[str, float]] = {}
    realized = 0.0
    fees = 0.0
    closed = 0
    leg_pnls: list[float] = []
    uncosted = False
    for lg in legs:
        st = state.setdefault(lg["symbol"], {"qty": 0.0, "cost": 0.0, "avg": 0.0})
        gross = lg["qty"] * lg["price"]
        fee = gross * fee_pct
        # ⭐ `since` 之前的腿**只用来累成本，不计入本窗口的收益/费用**（与 live 侧
        # 「成本从全历史累、窗口只切平仓日」完全同一个口径）。
        in_window = since is None or lg.get("at") is None or lg["at"] >= since
        if in_window:
            fees += fee
        if lg["side"] == "BUY":
            st["cost"] += gross + fee
            st["qty"] += lg["qty"]
        else:
            sold = lg["qty"]
            costed = min(sold, st["qty"])
            if costed < sold:
                uncosted = True          # 卖的比已知买入多 → 超出部分成本未知，不按 0 算
            if costed > 0:
                leg = (gross - fee) * (costed / sold) - st["avg"] * costed
                if in_window:
                    realized += leg
                    leg_pnls.append(round(leg, 6))
                    closed += 1
            st["qty"] = max(0.0, st["qty"] - sold)
            st["cost"] = st["avg"] * st["qty"]
        st["avg"] = (st["cost"] / st["qty"]) if st["qty"] > 0 else 0.0
    return {
        "realized_pnl": round(realized, 4),
        "fees": round(fees, 6),
        "closed_legs": closed,
        "closed_leg_pnls": leg_pnls,     # 提案后验的胜率要数正负（S4）
        "open_positions": {s: {"quantity": round(v["qty"], 8),
                               "avg_cost": round(v["avg"], 6)}
                           for s, v in state.items() if v["qty"] > 0},
        "has_uncosted_sell": uncosted,
    }


# ──────────────────────── 对外 ────────────────────────

def strategy_pnl(strategy_id: str, *, since: datetime | None = None,
                 until: datetime | None = None) -> dict[str, Any]:
    """这条策略赚了多少。**`basis` 一定要跟着数字一起读。**

    Returns:
        `basis` 四态：
          - `live_fills`       真金白银（`crypto_fills` 回放，费用口径正确）
          - `paper_simulated`  理想撮合的模拟账（⛔ 不可与 live 比大小）
          - `mixed`            两者都有，分块返回在 `live` / `paper` 里 —— **别相加**
          - `none`             这条策略还没有可算的成交

    🔴 **分桶依据是「数据本身」，不是 `row.mode`。** `mode` 是**当前**模式，而
    `arm()` / `enable_paper()` 随时会翻它，paper→arm→退回 paper 正是设计好的主流程
    （`00-PLAN §3` 裁决 7）。按 `row.mode` 分支的话，一条切回 paper 的策略会让它
    **真金白银赚的钱整段人间蒸发**，verdict 还会一句话之内自相矛盾。
    """
    row = _strategy_row(strategy_id)
    if row is None:
        return {"strategy_id": strategy_id, "basis": "none",
                "reason": f"没有 {strategy_id} 这条策略"}

    mode = (row.mode or "paper").lower()
    live = _live_block(strategy_id, since, until)
    # 🔴 **成本基础必须从全历史累，窗口只切平仓日** —— 与 `_live_order_ids` 同一个道理，
    # paper 侧原本没有这道保护：按 `since` 筛 run 会把**窗口外的买入腿**切掉，
    # `costed = min(sold, 0) = 0` → 那笔平仓不记 → 收益**系统性低估**
    # （实测窗口外买 1@100 + 窗口内买 1@100 + 窗口内卖 2@200：真实 +199.4，算出 +99.7）。
    # 偏差方向恒为低估 → 会把提案的 hit 判成 miss。
    legs = _paper_legs(_runs(strategy_id, None, until))
    paper = None
    if legs:
        paper = {"basis": "paper_simulated", "simulated_legs": len(legs),
                 **_pair(legs, _taker_fee(row), since=since),
                 "note": ("⚠️ 模拟账：按决策价理想撮合（挂单必成、零冲击、零排队），"
                          "手续费按策略 cost_model 计。"
                          "**不可与 live 的数字直接比大小。**")}

    head = {"strategy_id": strategy_id, "current_mode": mode}
    # `_live_block` 三种返回：None（名下没成交）／basis=none（有成交但算不出来，
    # 例如明细还没同步 —— 那是**必须说出口的**，不能被 paper 那块盖过去）／可用。
    live_ok = live is not None and live.get("basis") == "live_fills"
    if live_ok and paper:
        return {**head, "basis": "mixed", "live": live, "paper": paper,
                "note": ("这条策略既有实盘成交也有纸面记录（模式切换过）。"
                         "⛔ 两块**不可相加、也不可比大小** —— paper 是理想撮合。")}
    if live is not None:
        if live_ok:
            return {**head, **live}
        if paper:                 # live 算不出来 + 有 paper：两边都要露出来
            return {**head, "basis": "paper_simulated", **paper,
                    "live_unavailable": live.get("reason"),
                    "coverage": live.get("coverage")}
        return {**head, **live}   # 只有 live 且算不出来 —— 原样报出理由
    if paper:
        return {**head, **paper}
    _, unknown = _live_order_ids(strategy_id, since, until)
    return {**head, "basis": "none",
            "reason": "这条策略在窗口内既没有归属它的成交，也没产出过「本应成交」的决策",
            "coverage": {"attributed_trades": 0, "unknown_source_trades": unknown}}


def daily_returns(strategy_id: str, *, days: int = 90,
                  since: datetime | None = None,
                  until: datetime | None = None) -> dict[str, Any]:
    """这条策略的**日收益率序列** —— S3 四道门槛 + S4 提案后验都靠它。

    Args:
        days: 从**现在**往回数多少天（默认口径，竞技场用这个）。
        since / until: 显式窗口。🔴 **算历史窗口必须传这两个**：
            只传 `days` 的话窗口永远贴着「现在」，而一份 60 天前批准、兑现窗口 30 天的
            提案要看的是 `[T-60, T-30]` —— 拿 `days=30` 去取会取到 `[T-30, T]`，
            **完全错误的时段**，而且不会有任何报错。

    口径（五条都要一起读）：

    1. **分母是本金，不是浮动权益**。策略级的「权益曲线」在共享账户里根本切不出来
       （币安账户是一个池子，多条策略 + Jason 手动单混在一起）。所以这里算的是
       `当日已实现盈亏 / 本金` —— **一个近似**，但它是本项目当下唯一诚实算得出来的口径。
    2. 🔴 **只有已实现盈亏进序列，浮亏对它完全不可见。**
       后果是**系统性偏袒「赢了就跑、亏了死扛」的策略**：那种策略的序列是清一色正数 + 零，
       均值高、方差小、最大回撤 = 0，而它真实持仓可能挂着 -40%。
       所以返回值带 `open_positions`，**门槛④ 必须把它露出来**，别假装看得见浮亏。
    3. **没交易的日子是 0，不是缺失**。序列必须按自然日补齐（crypto 7×24 没有休市），
       否则「30 天里只有 3 天有交易」会被算成「3 个样本点、波动极小、显著性爆表」——
       那是量化里最经典的一种自欺。
       （补零不影响 t 统计量：`t ≈ sqrt(交易日数) × μ/σ`，与只取交易日等价。）
    4. **`days` 是「这条策略实际活了多久」，不是查询窗口长度。**
       🔴 第一版返回的是窗口天数，于是一条今天刚建的策略也显示「跑了 91 天」——
       门槛① 的天数那一半**永久失效**。
    5. **paper 和 live 各算各的**，`basis` 跟着返回。两者不可比（裁决 9 坑 1）。

    Returns:
        `{basis, dates, returns, capital, capital_basis, window_days, days,
          trade_days, trade_count, open_positions}`；算不出来时 `basis="none"`。
    """
    row = _strategy_row(strategy_id)
    if row is None:
        return {"basis": "none", "reason": f"没有 {strategy_id} 这条策略"}

    until = until or utc_now()
    since = since or (until - timedelta(days=max(1, int(days))))
    window = max(1, (until - since).days)
    capital = _capital_basis(row)
    if capital <= 0:
        # ⛔ 分母兜底成 1.0 会让「日收益率」变成**美元金额**（一天赚 500 → r=500），
        # 门槛②④ 和最大回撤全线失真。宁可诚实说算不出来。
        return {"basis": "none",
                "reason": "总资金设置为 0 或读不到，收益率没有分母可算"}
    daily: dict[str, float] = {}
    basis = "none"
    open_positions: dict[str, Any] = {}
    trade_count = 0

    order_ids, _unknown = _live_order_ids(strategy_id, since, until)
    if order_ids:
        from crypto_intel_engine import cost_basis as cb
        try:
            state = cb.replay(order_ids=order_ids)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"策略 {strategy_id} 日收益序列回放失败: {e}")
            state = {}
        if any(st["fills"] for st in state.values()):
            basis = "live_fills"
            for sym, st in state.items():
                for c in st["closed"]:
                    daily[c["date"]] = daily.get(c["date"], 0.0) + c["pnl"]
                    trade_count += 1
                if st["quantity"] > 0:
                    open_positions[sym] = {"quantity": round(st["quantity"], 8),
                                           "avg_cost": round(st["avg_cost"], 6)}

    if basis == "none":
        # 同 live 侧：**成本从全历史累**（`_runs(..., None, until)`），窗口只切平仓日 ——
        # 按 since 筛 run 会把窗口外的买入腿切掉，收益系统性低估。
        legs = _paper_legs(_runs(strategy_id, None, until))
        if legs:
            lo_key = market_day_of(since, CRYPTO).isoformat()
            for d, pnl in _paper_daily_pnl(legs, _taker_fee(row)):
                if d < lo_key:
                    continue                    # 窗口外的平仓只贡献成本，不进序列
                basis = "paper_simulated"
                daily[d] = daily.get(d, 0.0) + pnl
                trade_count += 1
            if basis == "none" and any(
                    lg.get("at") and market_day_of(lg["at"], CRYPTO).isoformat() >= lo_key
                    for lg in legs):
                basis = "paper_simulated"       # 窗口内有腿但都还没平仓
            if basis != "none":
                pos = _pair(legs, _taker_fee(row),
                            since=since).get("open_positions") or {}
                open_positions = dict(pos)

    if basis == "none":
        return {"basis": "none",
                "reason": "这条策略在窗口内没有可算收益的成交/纸面记录"}

    lo = market_day_of(since, CRYPTO)
    hi = market_day_of(until, CRYPTO)
    dates: list[str] = []
    rets: list[float] = []
    cur = lo
    while cur <= hi:
        key = cur.isoformat()
        dates.append(key)
        rets.append(round(daily.get(key, 0.0) / capital, 8))
        cur += timedelta(days=1)
    return {"basis": basis, "dates": dates, "returns": rets,
            "capital": capital, "capital_basis": (row.capital_basis or "config"),
            "window_days": len(dates),
            "days": _active_days(row, window),
            "trade_days": sum(1 for r in rets if r != 0.0),
            "trade_count": trade_count,
            "open_positions": open_positions,
            "note": ("日收益 = 当日已实现盈亏 / 本金（近似，不是真权益曲线）；"
                     "没交易的日子记 0 而不是缺失；"
                     "⚠️ **浮亏不在这个序列里** —— 看 open_positions。")}


def _active_days(row: Any, window: int) -> int:
    """这条策略**实际活了多久**（天），与查询窗口取小。

    🔴 别用窗口长度冒充它：那样一条今天刚建的策略也会显示「跑了 91 天」，
    门槛① 的天数那一半就永久失效了（默认窗口 90 天 > 阈值 30 天，恒过）。
    """
    created = getattr(row, "created_at", None)
    if created is None:
        return 0
    try:
        alive = (utc_now() - created).days
    except TypeError:      # created_at 是字符串（server_default 落的裸串）
        return min(window, 0)
    return max(0, min(window, int(alive)))


def _capital_basis(row: Any) -> float:
    """算收益率的分母 —— **统一用配置本金，对所有策略一把尺子**。

    两个刻意的取舍：

    1. ⛔ **不去连币安拿账户实时总值**。本模块开头声明的是「纯计算：不出网」，而
       `broker.connect()` 每次都真发一次账户签名请求；`evaluate_arena` 对每条策略各调
       一次，一个「该不该换策略」的判定就挂在币安可用性上了。
    2. ⭐ **所有策略共用同一个分母**，否则一条 `real_total_value`（比如 2000 USDT）
       对上一条 `config`（5000）时，前者的日收益率被整体放大 2.5 倍 ——
       门槛②④ 会给出**系统性错误**的结论，而「口径可比」那道门槛根本看不见分母差异。
       统一分母之后比例效应两边抵消，比较是 apples-to-apples 的。

    ⚠️ 代价：对声明了 `real_total_value` 的策略，**收益率的绝对量级会偏**
    （它按真实账户值定仓位，却按配置本金算收益率）。所以 `capital_basis` 一起返回，
    由「口径可比」那道门槛去卡两边是否同源。
    """
    from trading_engine.risk.adapter import get_total_capital
    try:
        return float(get_total_capital() or 0)
    except Exception as e:  # noqa: BLE001 — 读不到就返 0，由调用方判「算不出来」
        logger.warning(f"策略 {row.strategy_id} 取本金失败: {e}")
        return 0.0


def _paper_daily_pnl(legs: list[dict], fee_pct: float) -> list[tuple[str, float]]:
    """paper 腿 → [(日期, 当日已实现盈亏)]，配对规则与 `_pair` 完全一致。

    ⚠️ 不复用 `_pair` 是因为那个函数只回汇总值；这里要的是**按日拆开**。
    两处的配对规则必须一模一样，改一边就要改另一边（有测试钉死两者的总和相等）。
    """
    state: dict[str, dict[str, float]] = {}
    out: list[tuple[str, float]] = []
    for lg in legs:
        st = state.setdefault(lg["symbol"], {"qty": 0.0, "cost": 0.0, "avg": 0.0})
        gross = lg["qty"] * lg["price"]
        fee = gross * fee_pct
        day = (market_day_of(lg["at"], CRYPTO).isoformat() if lg.get("at")
               else market_day_of(utc_now(), CRYPTO).isoformat())
        if lg["side"] == "BUY":
            st["cost"] += gross + fee
            st["qty"] += lg["qty"]
        else:
            sold = lg["qty"]
            costed = min(sold, st["qty"])
            if costed > 0:
                out.append((day, (gross - fee) * (costed / sold) - st["avg"] * costed))
            st["qty"] = max(0.0, st["qty"] - sold)
            st["cost"] = st["avg"] * st["qty"]
        st["avg"] = (st["cost"] / st["qty"]) if st["qty"] > 0 else 0.0
    return out


def strategy_health(strategy_id: str, *, days: int = 30) -> dict[str, Any]:
    """体检报告：跑了多少 tick、为什么不开单、赚没赚、护栏状态，外加一句人话结论。"""
    row = _strategy_row(strategy_id)
    if row is None:
        return {"ok": False, "reason": f"没有 {strategy_id} 这条策略"}

    until = utc_now()
    since = until - timedelta(days=max(1, int(days)))
    runs = _runs(strategy_id, since, until)
    if not runs:
        # 「没跑过」≠「跑了但没赚」——这两件事给出的下一步动作完全不同。
        return {"ok": False, "strategy_id": strategy_id, "name": row.name,
                "window_days": days,
                "reason": (f"{row.name} 在最近 {days} 天里一条运行记录都没有。"
                           f"当前 enabled={row.enabled}、status={row.status} —— "
                           f"先确认它是不是压根没被武装/已退役，而不是「跑了但没成绩」。")}

    by_status: dict[str, int] = {}
    reasons: dict[str, int] = {}
    reason_texts: dict[str, str] = {}
    symbol_decisions = 0
    for r in runs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        for d in (r.get("detail") or {}).get("decisions") or []:
            symbol_decisions += 1
            st = d.get("status") or "unknown"
            reasons[st] = reasons.get(st, 0) + 1
            if d.get("reason") and st not in reason_texts:
                reason_texts[st] = str(d["reason"])

    staged = sum(r["orders_placed"] for r in runs)
    pnl = strategy_pnl(strategy_id, since=since, until=until)
    return {
        "ok": True,
        "strategy_id": strategy_id, "name": row.name,
        "mode": row.mode, "status": row.status, "enabled": bool(row.enabled),
        # `utc_iso` 而不是裸 `.isoformat()`：naive UTC 直接序列化会丢 offset，
        # 前端按本地时间解读就错 8 小时（`docs/CODING_STANDARDS.md` §11.4）。
        "window": {"days": days, "since": utc_iso(since), "until": utc_iso(until)},
        "runs": {"total": len(runs), "by_status": by_status},
        "symbol_decisions": symbol_decisions,
        "no_order_reasons": {
            k: {"count": v, "means": _NO_ORDER_LABELS.get(k, k),
                "example": reason_texts.get(k)}
            for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]) if k != _READY
        },
        "orders_staged": staged,
        "pnl": pnl,
        "guardrails": {
            "consecutive_trips": row.consecutive_guardrail_trips or 0,
            "halted_reason": row.halted_reason,
        },
        "verdict": _verdict(row, runs, reasons, staged, pnl),
    }


def _verdict(row: Any, runs: list[dict], reasons: dict[str, int],
             staged: int, pnl: dict) -> str:
    """一句人话结论 —— 屏幕上任一数字，3 秒内说不出「所以我该干嘛」就不配摆出来
    （`00-PLAN §3` 裁决 16）。这套东西只服务 Jason 一人，**所以它必须解释**。
    """
    total_dec = sum(reasons.values())
    parts: list[str] = []
    if row.status == "paused_by_guardrail":
        parts.append(f"⛔ 这条策略已被护栏熔断停机（{row.halted_reason or '原因未记录'}），"
                     f"下面的数字是停机前的。")
    if not total_dec:
        parts.append(f"{len(runs)} 次运行里没有任何逐币决策明细——大概率每次都在"
                     f"入口就被拦下了（看 runs.by_status）。")
        return " ".join(parts)

    # 🔴 **「开了几单」不能只看 `staged`**：paper 模式下 `_stage_or_log` 直接 return None，
    # `orders_placed` **恒为 0** —— 拿它当判据会让一条触发了 N 次、还赚了钱的 paper 策略
    # 被诊断成「条件根本没被触发过，阈值定得太严」，**给出的下一步动作是反的**。
    # paper 的等价物是 `ready` 决策数。
    ready = reasons.get(_READY, 0)
    produced = staged or ready
    top = max((kv for kv in reasons.items() if kv[0] != _READY), key=lambda kv: kv[1],
              default=None)
    if produced == 0 and top:
        share = top[1] / total_dec
        parts.append(
            f"{total_dec} 次逐币评估**一单都没开**，其中 {top[1]} 次（{share:.0%}）是"
            f"「{_NO_ORDER_LABELS.get(top[0], top[0])}」。")
        if top[0] == "blocked_cost":
            parts.append("这是**策略自身的边际太薄**，不是行情不好——"
                         "要么放宽入场条件让它抓更大的波段，要么直接退役它。")
        elif top[0] == "no_signal":
            parts.append("条件根本没被触发过——要么阈值定得太严，要么这段行情就不属于它。")
        elif top[0] in ("blocked_risk", "blocked_guardrail"):
            parts.append("卡在风控/护栏而不是行情——先去看是不是仓位或敞口设置有冲突。")
        elif top[0] == "skipped_dup":
            parts.append("条件一直在触发，但上一张同向单还挂着没结——"
                         "先去待确认单那儿把它处理掉，不然后面全被挡住。")
    elif staged:
        # ⚠️ 只答了一半：这是**排队待确认**的张数，不是成交张数（被拒 10 张和成交
        # 10 张在这个数字上一模一样）。成交侧看下面的盈亏段。
        parts.append(f"{total_dec} 次逐币评估排出 {staged} 张待确认单（是否成交看下面）。")
    else:
        parts.append(f"{total_dec} 次逐币评估里 {ready} 次判定「本应成交」（纸面干跑，未真排单）。")

    parts.append(_pnl_sentence(pnl))
    return " ".join(parts)


def _pnl_sentence(pnl: dict) -> str:
    basis = pnl.get("basis")
    if basis == "mixed":
        live, paper = pnl.get("live") or {}, pnl.get("paper") or {}
        return (f"实盘已实现 {live.get('realized_pnl', 0):+.2f} USDT"
                f"（{live.get('closed_legs', 0)} 笔平仓）；另有模拟账 "
                f"{paper.get('realized_pnl', 0):+.2f} USDT（模式切换过）——"
                f"⛔ 两者**不可相加也不可比大小**。")
    if basis == "live_fills":
        s = (f"真实已实现盈亏 {pnl.get('realized_pnl', 0):+.2f} USDT"
             f"（{pnl.get('closed_legs', 0)} 笔平仓）。")
        if pnl.get("has_uncosted_sell"):
            # 这个标志此前从不进 verdict —— 于是「买入腿不在账里」会被念成「赚了 0」。
            s += "⚠️ 其中有卖出找不到对应的买入记录，成本未知，那部分**没算进盈亏**。"
        if pnl.get("coverage", {}).get("unknown_source_trades"):
            s += (f"⚠️ 同期还有 {pnl['coverage']['unknown_source_trades']} 笔成交"
                  f"来源不明，没算进这条策略。")
        return s
    if basis == "paper_simulated":
        s = (f"模拟账 {pnl.get('realized_pnl', 0):+.2f} USDT——"
             f"⚠️ 理想撮合，**别拿它跟实盘策略比大小**。")
        if pnl.get("live_unavailable"):
            s += f"（实盘那部分算不出来：{pnl['live_unavailable']}）"
        return s
    return f"盈亏还算不出来：{pnl.get('reason')}"


# 软退役之后这些状态的行会**永久累积**（旧实现是直接删的），默认不列，
# 否则跑上半年这张表全是历史版本。要看历史用 `include_archived=True`。
_ARCHIVED_STATUSES = ("retired", "superseded")


def standings(*, days: int = 30, include_archived: bool = False) -> list[dict[str, Any]]:
    """所有策略的战绩排行（S3 的竞技场会扩它）。

    ⛔ **这里刻意不排序、不评优劣**：paper 和 live 不可比、样本量差异巨大，
    现在就按某个数排序等于诱导「追逐近期最优」（`00-PLAN §3` 裁决 9 的坑 3）。
    排名规则是 S3 的四道门槛，不是一个 `sorted()`。
    """
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoStrategy
    session = get_session()
    try:
        q = session.query(CryptoStrategy)
        if not include_archived:
            q = q.filter(CryptoStrategy.status.notin_(_ARCHIVED_STATUSES))
        # ⚠️ 带上 family/version：多版本同名（fork 默认继承名字），
        # 只给 name 的话 AI 和 Jason 都分不清哪条是哪版。
        # ⭐ `is_benchmark` 一起带出来：调用方（交易终端要把基准线置顶、并给它挂个
        # 徽章）否则只能逐条再查一次库 —— 那是 N+1，而且「哪条是尺子」这件事
        # 不标出来，裁决 8 的价值（一眼看到 AI 有没有输给你手写那条）就没了。
        meta = [(r.strategy_id, r.name, r.mode, r.status, bool(r.enabled),
                 r.family_id or r.strategy_id, r.version or 1, bool(r.is_benchmark))
                for r in q.order_by(CryptoStrategy.created_at.asc()).all()]
    finally:
        session.close()

    out = []
    for sid, name, mode, status, enabled, family, version, is_benchmark in meta:
        h = strategy_health(sid, days=days)
        out.append({
            "strategy_id": sid, "name": name, "version": version, "family_id": family,
            "mode": mode, "status": status, "enabled": enabled,
            "is_benchmark": is_benchmark,
            "runs": h.get("runs", {}).get("total", 0) if h.get("ok") else 0,
            "orders_staged": h.get("orders_staged", 0) if h.get("ok") else 0,
            "pnl": h.get("pnl") if h.get("ok") else None,
            # ⚠️ 盈亏是**这个窗口内**的，不是「开仓以来」。数字旁边不写窗口，
            # 就会跟四道门槛（默认 90 天）的判定摆在同一行互相打架。
            "window_days": days,
            "verdict": h.get("verdict") or h.get("reason"),
        })
    return out

"""CryptoStrategyService —— 策略 CRUD + spec 序列化 + 回测闸 + arm 武装。

编译工具（`agents/tools/crypto_strategy_tools.py`）与 API 路由（`api/routes/crypto_strategy.py`）
都调这一份，业务逻辑不重复。DSL 子对象以 JSON 串存 Text 列，读回时重建 pydantic。
"""
import json
from datetime import timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func, or_

from common.market import CRYPTO
from common.market_time import market_today, utc_iso, utc_now
from crypto_intel_engine.dsl import CryptoStrategySpec
from crypto_strategy.backtest_gate import run_backtest_gate


class StrategyError(Exception):
    """业务拒绝（如未过回测就想 arm）——路由转 400，工具转 negative。"""


# ──────────────────── spec ↔ row 序列化 ────────────────────

_SUBOBJECTS = ("universe", "entry_rules", "exit_rules", "position_policy",
               "cost_model", "guardrails")


def _spec_to_columns(spec: CryptoStrategySpec) -> dict[str, Any]:
    """CryptoStrategySpec → CryptoStrategy 列 dict（子对象转 JSON 串）。"""
    cols: dict[str, Any] = {
        "name": spec.name, "strategy_kind": spec.strategy_kind,
        "interval_minutes": spec.interval_minutes,
        "capital_basis": spec.capital_basis, "mode": spec.mode,
    }
    for key in _SUBOBJECTS:
        cols[key] = getattr(spec, key).model_dump_json()
    return cols


def spec_from_row(row) -> CryptoStrategySpec:
    """CryptoStrategy 行 → CryptoStrategySpec（重建 + 再校验）。"""
    data: dict[str, Any] = {
        "name": row.name, "strategy_kind": row.strategy_kind,
        "interval_minutes": row.interval_minutes,
        "capital_basis": row.capital_basis, "mode": row.mode,
    }
    for key in _SUBOBJECTS:
        raw = getattr(row, key)
        if raw:
            data[key] = json.loads(raw)
    return CryptoStrategySpec(**data)


def _gen_id() -> str:
    import secrets
    return f"CS-{utc_now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


def _gen_family_id() -> str:
    import secrets
    return f"SF-{utc_now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


# 同 family 里**最多只能有一个**处于这些状态（新版本上线时旧版本要让位）。
_LIVE_STATUSES = ("armed", "paused_by_guardrail")


def _family_filter(row):
    """匹配同族的 SQL 条件。

    ⚠️ 带 `strategy_id == family` 这条兜底是刻意的：`family_id` 允许为 NULL（存量行靠
    `init_db` 回填），万一某个新插入点忘了传，`family` 会回落成 `strategy_id` ——
    那时只按 `family_id` 匹配会**一条都匹配不到**，于是 v1 和 v2 同时 armed，
    而 `pending.has_open` 的去重是按 `strategy_id` 的，拦不住跨版本重复下单。
    """
    from sqlalchemy import or_

    from data_engine.storage.models import CryptoStrategy
    family = row.family_id or row.strategy_id
    return or_(CryptoStrategy.family_id == family,
               CryptoStrategy.strategy_id == family)


def _supersede_siblings(session, row, *, target_mode: str) -> list[str]:
    """让位：返回被退位的 strategy_id 列表。

    两条规则叠加：

    1. **同族**其它在跑的版本一律退位 —— 同一条策略不该有两个版本同时跑。
    2. 🔴 **上 live 时，全局其它跑实盘的也要退位**（裁决 7：同期只有一条 live）。
       只按同族让位的话，「从甲家族换到乙家族」会留下**两条同时 live**，而且
       `pending.has_open` 的去重是按 strategy_id 的，两条会对同一个币各排一张单。
       这是复审实测出来的：arm 甲、再 arm 乙（不同族）→ 两条都是 armed+live+enabled。
    """
    from data_engine.storage.models import CryptoStrategy
    cond = _family_filter(row)
    if target_mode == "live":
        cond = or_(cond, CryptoStrategy.mode == "live")
    out = []
    for other in session.query(CryptoStrategy).filter(
            cond,
            CryptoStrategy.strategy_id != row.strategy_id,
            CryptoStrategy.status.in_(_LIVE_STATUSES)).all():
        other.status = "superseded"
        other.enabled = 0
        out.append(other.strategy_id)
    return out


def _current_live(session, row) -> str | None:
    """arm 之前，**全局**正在跑实盘的是哪一条（用来记「谁换了谁」）。

    ⚠️ 不限同族：同期只有一条 live（裁决 7），换到另一个家族同样是一次切换 ——
    按同族查会让跨家族切换查不到「被换下的人」，冷却期也就无从算起。
    """
    from data_engine.storage.models import CryptoStrategy
    cur = session.query(CryptoStrategy).filter(
        CryptoStrategy.strategy_id != row.strategy_id,
        CryptoStrategy.enabled == 1,
        CryptoStrategy.mode == "live",
        CryptoStrategy.status.in_(_LIVE_STATUSES)).first()
    return cur.strategy_id if cur else None


def _challenger_count(session, row) -> int:
    """切换那一刻有几个挑战者 —— 多重比较修正的输入，事后复盘要能还原当时的门槛高度。"""
    from data_engine.storage.models import CryptoStrategy
    return session.query(CryptoStrategy).filter(
        CryptoStrategy.enabled == 1,
        CryptoStrategy.strategy_id != row.strategy_id,
        CryptoStrategy.is_benchmark != 1).count()


def _record_switch(prev_live: str | None, out: dict, superseded: list[str],
                   challenger_count: int) -> None:
    """把这次上位记进 `crypto_arena_switches` —— **冷却期靠它算，事后复盘也只有它**。

    ⚠️ 只在**真的把一条在跑实盘的策略换下来**时记。判据是 `prev_live` 而不是
    「superseded 非空」：`enable_paper` 会把 status 设成 `armed`，于是首次 arm 一条
    同族有 paper 兄弟的策略也会 supersede 到它 → 被当成一次切换 →
    **14 天冷却期从第 0 天就开始倒计时**，把「刚建好想调一下」直接挡掉。
    """
    if not prev_live:
        return
    from data_engine.storage.database import get_session
    from data_engine.storage.models import CryptoArenaSwitch
    session = None
    try:
        # ⚠️ `get_session()` 也要在 try 里：它抛异常会让一个**已经 commit 成功**的
        # arm 返回 500。
        session = get_session()
        session.add(CryptoArenaSwitch(
            family_id=out.get("family_id"),
            from_strategy_id=prev_live,
            to_strategy_id=out["strategy_id"],
            gates_snapshot=_gates_snapshot(prev_live, out["strategy_id"]),
            challenger_count=challenger_count,
            note=f"arm v{out.get('version')}；让位 {superseded or '无'}"))
        session.commit()
    except Exception as e:  # noqa: BLE001 — 留痕失败不该掀翻已经生效的 arm
        logger.warning(f"策略切换留痕失败 {out.get('strategy_id')}: {e}")
    finally:
        if session is not None:
            session.close()


def _gates_snapshot(from_id: str, to_id: str) -> str | None:
    """切换那一刻四道门槛各是多少 —— **事后复盘「这次换对了吗」的唯一依据**。

    ⚠️ 尽力而为：算不出来就留空，绝不因此拖垮 arm。注意这里的判定是**回溯性的**
    （新版本此刻已经是 armed 了），所以它记的是「换的时候两边的数据长什么样」，
    不是「规则当时批准了这次切换」—— 切换本来也不需要规则批准（规则只给意见）。
    """
    try:
        import json

        from crypto_strategy.arena import evaluate_challenger
        from crypto_strategy.performance import daily_returns
        snaps = {}
        for sid in (from_id, to_id):
            dr = daily_returns(sid)
            snaps[sid] = {"basis": dr.get("basis"), "returns": dr.get("returns") or [],
                          "capital_basis": dr.get("capital_basis"),
                          "days": dr.get("days") or 0,
                          "trade_count": dr.get("trade_count") or 0,
                          "open_positions": dr.get("open_positions") or {},
                          "backtest_passed": True}
        r = evaluate_challenger(snaps[to_id], snaps[from_id])
        return json.dumps({"gates": r["gates"], "blocked_by": r["blocked_by"]},
                          ensure_ascii=False, default=str)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"切换门槛快照算不出来 {from_id}→{to_id}: {e}")
        return None


def _assert_armable(row) -> None:
    """已退役的策略不许被 arm/enable 复活 —— 退役是 Jason 的决定，不该被一次上线动作推翻。"""
    if row.status == "retired":
        raise StrategyError(
            f"{row.strategy_id} 已退役，不能直接上线。要重新启用请先基于它 fork 一个新版本。")


def backtest_metrics_json(bt: dict[str, Any]) -> str:
    """回测结果 → `backtest_metrics` 列的 JSON。**只此一处**，别再抄第二份。

    🔴 **`basis` / `engine_version` 必须落库**（2026-07-31 补）。

    S8 把 `net_return` 的口径从「各币独立收益的算术平均」换成了「组合收益
    （共享一份资金）」，两者**不可比**。第一版只把这两个标记放在返回值里，
    落库那层一个字都没留 —— 于是库里 S8 前后的行**结构上无法区分**：
    同一列 `backtest_net_return`、同一个 JSON、含义已换。
    谁要是把新旧数字拉出来比大小，会得到一个完全错误的结论而且看不出来。
    """
    return json.dumps(
        {"metrics": bt.get("metrics"), "degraded": bt.get("degraded"),
         "degraded_reasons": bt.get("degraded_reasons"),
         # caveats 与 degraded 正交：规则回放了，但数字本身的含义比看上去弱
         # （费率口径对不上 / 大量日子结构上不可能开仓）
         "caveats": bt.get("caveats"),
         # ⭐ 口径标记：没有它，历史行与新行就分不出来了
         "basis": bt.get("basis"),
         "engine_version": bt.get("engine_version"),
         # 组合口径特有：几天被现金/仓位上限卡住（币数越多越紧）
         "cash_contention": bt.get("cash_contention"),
         "cap_contention": bt.get("cap_contention"),
         "per_symbol": bt.get("per_symbol")}, ensure_ascii=False)


def _apply_backtest(row, bt: dict[str, Any]) -> None:
    """把回测结果写进策略行 —— `compile_and_persist` 与 `fork_version` 共用一份。"""
    row.last_backtest_at = utc_now()
    row.backtest_net_return = bt.get("net_return")
    row.backtest_metrics = backtest_metrics_json(bt)
    row.backtest_passed = 1 if bt.get("passed") else 0
    row.status = "backtested"


def row_summary(row) -> dict[str, Any]:
    """给前端/工具的轻量摘要（不含 JSON 全文）。"""
    return {
        "strategy_id": row.strategy_id, "name": row.name, "mode": row.mode,
        "family_id": row.family_id or row.strategy_id, "version": row.version or 1,
        "forked_from": row.forked_from, "is_benchmark": bool(row.is_benchmark),
        "status": row.status, "enabled": bool(row.enabled),
        "strategy_kind": row.strategy_kind, "interval_minutes": row.interval_minutes,
        "backtest_passed": bool(row.backtest_passed),
        "backtest_net_return": row.backtest_net_return,
        # 带 offset，前端 utils/datetime.ts 负责转本地（裸串会被 JS 当本地时间解析）
        "last_run_at": utc_iso(row.last_run_at),
        "halted_reason": row.halted_reason,
    }


# ──────────────────── 服务 ────────────────────

class CryptoStrategyService:
    def _session(self):
        from data_engine.storage.database import get_session
        return get_session()

    # ---- 回测（技术代理 + 真实费率）----
    def _load_bars(self, symbols: list[str], lookback_days: int = 400) -> dict[str, list[dict]]:
        from data_engine.engine import DataEngine
        # crypto 日线按 UTC 日落库（币安 klines openTime），回测窗口也按 UTC 日切
        today = market_today(CRYPTO)
        end = today.isoformat()
        start = (today - timedelta(days=lookback_days)).isoformat()
        out: dict[str, list[dict]] = {}
        engine = DataEngine()
        try:
            for sym in symbols:
                try:
                    df = engine.get_daily_data(sym, start, end, db_only=True)
                    if df is not None and not df.empty:
                        d = df.reset_index() if df.index.name else df
                        cols = [c for c in ("date", "open", "high", "low", "close", "volume")
                                if c in d.columns]
                        recs = d[cols].to_dict("records")
                        for r in recs:
                            if "date" in r and hasattr(r["date"], "strftime"):
                                r["date"] = r["date"].strftime("%Y-%m-%d")
                        out[sym] = recs
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"回测取 bars 失败 {sym}: {e}")
        finally:
            engine.close()
        return out

    def backtest(self, spec: CryptoStrategySpec) -> dict[str, Any]:
        bars = self._load_bars(spec.universe.symbols)
        return run_backtest_gate(spec, bars)

    # ---- 编译落库（校验已在 spec 构造时完成）----
    def compile_and_persist(self, spec: CryptoStrategySpec, description_nl: str | None = None,
                            do_backtest: bool = True) -> dict[str, Any]:
        from data_engine.storage.models import CryptoStrategy

        bt = self.backtest(spec) if do_backtest else None
        session = self._session()
        try:
            sid = _gen_id()
            row = CryptoStrategy(strategy_id=sid, description_nl=description_nl,
                                 # 全新策略 = 一族的第一版。fork 出的新版本走 `fork_version`。
                                 family_id=_gen_family_id(), version=1,
                                 enabled=0, status="draft", **_spec_to_columns(spec))
            if bt is not None:
                _apply_backtest(row, bt)
            session.add(row)
            session.commit()
            return {"strategy_id": row.strategy_id, "summary": row_summary(row), "backtest": bt}
        finally:
            session.close()

    # ---- fork：在某个版本基础上生成下一版（S2）----
    def fork_version(self, base_strategy_id: str, spec: CryptoStrategySpec, *,
                     description_nl: str | None = None,
                     do_backtest: bool = True) -> dict[str, Any]:
        """把新 spec 落成同 family 的下一个版本。

        ⛔ **绝不自动上线**：新版本 `enabled=0`、`status=draft`/`backtested`，
        旧版本**原样继续跑**。「批准了提案」和「让它上线」是两个决定
        （S2 §2.2）—— 上线要 Jason 再点一次 arm。
        """
        from data_engine.storage.models import CryptoStrategy

        session = self._session()
        try:
            base = self._get_row(session, base_strategy_id)
            family = base.family_id or base.strategy_id
            base_desc = base.description_nl
        finally:
            session.close()

        bt = self.backtest(spec) if do_backtest else None
        session = self._session()
        try:
            # ⚠️ 版本号在**插入的同一个 session 里**重新取 max —— 回测可能跑几十秒，
            # 用回测之前读到的数会让并发 apply 出两个同号版本（那个索引不是唯一索引，
            # DB 不挡），而 `latest_only` 只会留下其中一条，另一条永久隐身。
            top = session.query(func.max(CryptoStrategy.version)).filter(
                or_(CryptoStrategy.family_id == family,
                    CryptoStrategy.strategy_id == family)).scalar() or 1
            row = CryptoStrategy(
                strategy_id=_gen_id(), family_id=family, version=int(top) + 1,
                # 血缘：直接调 fork（不走提案）时，「v4 是从哪一版分出来的」只在返回值里，
                # 落一份到策略行上才查得回来。
                description_nl=description_nl or base_desc,
                enabled=0, status="draft", **_spec_to_columns(spec))
            row.forked_from = base.strategy_id
            if bt is not None:
                _apply_backtest(row, bt)
            session.add(row)
            session.commit()
            return {"strategy_id": row.strategy_id, "family_id": family,
                    "version": row.version, "base_strategy_id": base_strategy_id,
                    "summary": row_summary(row), "backtest": bt}
        finally:
            session.close()

    # ---- 读 ----
    def list_strategies(self, *, mode: str | None = None, enabled: bool | None = None,
                        latest_only: bool = True) -> list[dict]:
        """默认**每个 family 只列当前版本** —— 否则版本一多列表就被历史版本淹掉。

        `latest_only=False` 看全部历史版本（S3 竞技场比 v1 vs v2 时要）。
        """
        from data_engine.storage.models import CryptoStrategy
        session = self._session()
        try:
            q = session.query(CryptoStrategy)
            if mode:
                q = q.filter(CryptoStrategy.mode == mode)
            if enabled is not None:
                q = q.filter(CryptoStrategy.enabled == (1 if enabled else 0))
            rows = q.order_by(CryptoStrategy.created_at.desc()).all()
            if latest_only:
                # 「当前版本」= 同 family 里 version 最大的那个。
                # 🔴 **但必须并上所有 `enabled=1` 的行**（S2 复审补）：v1 armed 正在排真单、
                # AI 刚 apply 出 v2(draft) 时，只取最大版号会把**那条真在下单的 v1 藏起来** ——
                # Jason 在 App 里看到一条未启用的草稿，而真在跑的那条看不到、也 pause 不了。
                best: dict[str, Any] = {}
                for r in rows:
                    fam = r.family_id or r.strategy_id
                    cur = best.get(fam)
                    if cur is None or (r.version or 1) > (cur.version or 1):
                        best[fam] = r
                keep = set(best.values()) | {r for r in rows if r.enabled}
                rows = sorted(keep, key=lambda r: r.created_at or "", reverse=True)
            return [row_summary(r) for r in rows]
        finally:
            session.close()

    def list_family(self, family_id: str) -> list[dict]:
        """一族里的全部版本，按 version 升序 —— 「改完变好了没」的数据入口。"""
        from data_engine.storage.models import CryptoStrategy
        session = self._session()
        try:
            rows = session.query(CryptoStrategy).filter(
                CryptoStrategy.family_id == family_id).order_by(
                    CryptoStrategy.version.asc()).all()
            return [row_summary(r) for r in rows]
        finally:
            session.close()

    def get_strategy(self, strategy_id: str) -> dict:
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            detail = row_summary(row)
            detail["spec"] = spec_from_row(row).model_dump()
            detail["description_nl"] = row.description_nl
            detail["backtest_metrics"] = json.loads(row.backtest_metrics) if row.backtest_metrics else None
            return detail
        finally:
            session.close()

    # ---- 生命周期 ----
    def arm(self, strategy_id: str) -> dict:
        """武装 live：开始按 DSL 规则产**待确认单**（每笔仍由 Jason 逐笔确认才成交）。

        ⛔ 半自动系统里安全靠**逐笔确认 + 护栏 + 硬风控**，不靠回测——故 arm **不卡回测**
        （回测是双均线代理、不测你的 DSL 规则，仅供参考）。烂策略只会提烂建议、被你拒掉，
        赔不了钱。想纯观察不产真单用 enable_paper。

        ⭐ **同 family 永远最多一个 armed**（S2）：arm 一个版本时，同族其它在跑的版本
        自动置 `superseded` + `enabled=0`。不这么做的话 v1 和 v2 会同时对同一批币下单
        （`has_open` 的去重是按 strategy_id 的，拦不住跨版本重复）。
        """
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            _assert_armable(row)
            # ⚠️ 顺序不能反：先记下「谁在跑」，再让它退位。
            prev_live = _current_live(session, row)
            challengers = _challenger_count(session, row)
            superseded = _supersede_siblings(session, row, target_mode="live")
            row.mode = "live"
            row.enabled = 1
            row.status = "armed"
            row.halted_reason = None
            session.commit()
            out = row_summary(row)
            out["superseded"] = superseded
        finally:
            session.close()
        # 切换留痕（S3）：**冷却期靠它算**，事后复盘「这次换对了吗」也只有它。
        # 放在 commit 之后、独立 session 里：留痕失败不该把已经生效的 arm 回滚掉。
        _record_switch(prev_live, out, superseded, challengers)
        return out

    def enable_paper(self, strategy_id: str) -> dict:
        """纸面启用（不需回测通过，纯模拟不动真钱）。

        ⭐ **同样要让位**（S2 复审补）：不这么做的话 v1(live) 和 v2(paper) 会同族并存、
        引擎两条都跑。钱是安全的（paper 分支不排真单），但「同 family 最多一个在跑」
        这条不变式在文档里是**无条件**的，而且 `superseded` 的行能被 enable_paper 直接复活。
        """
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            _assert_armable(row)
            superseded = _supersede_siblings(session, row, target_mode="paper")
            row.mode = "paper"
            row.enabled = 1
            row.status = "armed"
            row.halted_reason = None
            session.commit()
            out = row_summary(row)
            out["superseded"] = superseded
            return out
        finally:
            session.close()

    def pause(self, strategy_id: str) -> dict:
        return self._set_state(strategy_id, enabled=0, status="draft")

    def set_benchmark(self, strategy_id: str, is_benchmark: bool = True) -> dict:
        """把一条策略标成**基准线**（裁决 8：Jason 手写的那条永久置顶当尺子）。

        ⭐ **没有基准线的胜率是自说自话** —— 如果只有 AI 能写策略，就永远不知道 AI 有没有
        价值。基准线不参与切换（永不被淘汰、也永不上位），只在排行里当尺子。
        AI 长期输给随手写的双均线，这个数字最刺眼，也最该被看见。
        """
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            row.is_benchmark = 1 if is_benchmark else 0
            session.commit()
            return row_summary(row)
        finally:
            session.close()

    def retire(self, strategy_id: str) -> dict:
        """退役 = **停跑并归档**，🔴 2026-07-27（S2）从「彻底删除」改成软退役。

        做三件事：`status=retired` + `enabled=0` + **清掉它还没被确认的待确认单**
        （那些必须清 —— 留着 Jason 一点确认就会给一条已退役的策略成交）。

        ⭐ **`crypto_strategy_runs` 一行不删。** 旧实现连运行日志一起 `delete`，
        于是退役即失忆：S1 的战绩、S3 竞技场的历史对比全没了。而 AI 提案样本一年才
        12-24 条（`00-PLAN §4` 问题 2），删一条少一条。
        真要腾空间用 `delete_strategy()`（显式、不可恢复）。

        🔴 **只删 `PENDING`。`EXECUTING` / `STALE` 一律留着**（S2 复审补）——
        `pending.py` 的注释是写死的禁令：「搁浅的 EXECUTING 绝不能删、也绝不能当失败」。
        Jason 点了确认、币安调用还在途（或后端此刻重启）时把行删掉，`_finalize()` 会
        找不到行静默 no-op、`cleanup()` 也再没机会把它标成 STALE 发对账告警 ——
        **一笔可能已经在币安成交的真单，本地线索直接归零**。
        """
        from data_engine.storage.models import CryptoPendingOrder
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)   # 不存在则抛 StrategyError
            dropped = session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.strategy_id == strategy_id,
                CryptoPendingOrder.status == "PENDING").delete(synchronize_session=False)
            kept = [r.order_ref for r in session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.strategy_id == strategy_id,
                CryptoPendingOrder.status.in_(("EXECUTING", "STALE"))).all()]
            row.enabled = 0
            row.status = "retired"
            session.commit()
            out = row_summary(row)
            out["pending_orders_dropped"] = dropped
            out["pending_orders_kept"] = kept
            if kept:
                out["warning"] = (f"{len(kept)} 张单处于在途/状态未知（{'、'.join(kept)}），"
                                  f"**没有删** —— 它们可能已经在币安成交，请去核对。")
            return out
        finally:
            session.close()

    def delete_strategy(self, strategy_id: str) -> dict:
        """**彻底删除**：连待确认单 + 运行日志一起清，不可恢复。

        ⚠️ 删掉运行日志 = 删掉这条策略的全部战绩证据，S3 竞技场再也比不到它。
        正常「不想跑了」用 `retire()`（软退役，数据留着）。这个入口留给「建错了/测试残留」。
        """
        from data_engine.storage.models import CryptoPendingOrder, CryptoStrategyRun
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            session.query(CryptoPendingOrder).filter(
                CryptoPendingOrder.strategy_id == strategy_id).delete(synchronize_session=False)
            runs = session.query(CryptoStrategyRun).filter(
                CryptoStrategyRun.strategy_id == strategy_id).delete(synchronize_session=False)
            session.delete(row)
            session.commit()
            return {"strategy_id": strategy_id, "status": "deleted", "deleted": True,
                    "runs_deleted": runs}
        finally:
            session.close()

    def run_and_store_backtest(self, strategy_id: str) -> dict:
        """按需重跑回测并回写（PUT 改 DSL 后 / 手动触发）。"""
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            spec = spec_from_row(row)
            bt = self.backtest(spec)
            row.last_backtest_at = utc_now()
            row.backtest_net_return = bt.get("net_return")
            # ⚠️ 与 `_apply_backtest` 共用同一个序列化器 —— 这两处从前各抄一份，
            #    加字段时很容易只改一边（`basis`/`engine_version` 就差点这样漏掉）
            row.backtest_metrics = backtest_metrics_json(bt)
            row.backtest_passed = 1 if bt.get("passed") else 0
            if row.status in ("draft",):
                row.status = "backtested"
            session.commit()
            return {"summary": row_summary(row), "backtest": bt}
        finally:
            session.close()

    # ---- 内部 ----
    def _set_state(self, strategy_id: str, *, enabled: int, status: str) -> dict:
        session = self._session()
        try:
            row = self._get_row(session, strategy_id)
            row.enabled = enabled
            row.status = status
            session.commit()
            return row_summary(row)
        finally:
            session.close()

    def _get_row(self, session, strategy_id: str):
        from data_engine.storage.models import CryptoStrategy
        row = session.query(CryptoStrategy).filter(
            CryptoStrategy.strategy_id == strategy_id).first()
        if not row:
            raise StrategyError(f"策略不存在：{strategy_id}")
        return row


crypto_strategy_service = CryptoStrategyService()

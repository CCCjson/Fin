"""P0-4 决策留痕卫生的门禁 —— 「订单回执永远不许被当成 AI 建议评」。

背景（2026-07-27 查生产库）：`decision_logs` 里 40 行
`BTCUSDT.BN BUY entry_price=100.0` 是 crypto 的**成交/挂单回执**（外加测试污染的
假成交），占全表 40%。它们当时因为 bar 不够全是 `unable`，**攒够 5 根就会被
`outcome_eval` 评成 `(65000-100)/100 ≈ +64900%` 的 win** → `get_decision_stats` 胜率
直奔 100% → P0-3 的 `compute_calibration`「只下调不上抬」看到 100% 命中 →
`calibration_factor` 恒 1.0 → **置信度校准对 crypto 永久失效，且不报错**。

本文件盯死四件事：
1. `execution` / `ops` 的行永不进 `backfill_outcomes` 的候选集；
2. 胜率的**分母**里也没有它们（只堵回填不够 —— 回执的 outcome_status 恒为 NULL，
   会被 `total` / `pending` 全额计入）；
3. 那颗具体的炸弹（entry=100 的 BTC）确实拆了；
4. 受确认门管的工具**全部**显式归类，新增一个忘登记就红。

⚠️ 全程用 conftest 的临时库，绝不碰生产 `data/market.db`（这张卡的成因之一就是
测试写进了生产库，见 `tests/conftest.py` 文首）。
"""
import os
import re
import sys
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import decision_log  # noqa: E402
from common.decision_kind import (  # noqa: E402
    ADVICE,
    CONFIRMED_TOOL_KINDS,
    ENTRY_KINDS,
    EVALUABLE_KINDS,
    EXECUTION,
    OPS,
    kind_for_confirmed_tool,
    normalize,
)
from data_engine.storage.database import Base  # noqa: E402
from data_engine.storage.models import DailyQuote, DecisionLog  # noqa: E402

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(decision_log, "get_session", lambda: session_factory())
    return session_factory


def _seed(s, *, kind, symbol="600519.SH", action="BUY", entry=100.0, days_ago=30,
          source="moneybill_recommend"):
    s.add(DecisionLog(
        decision_id=f"d{datetime.now().timestamp()}{symbol}{kind}{days_ago}",
        created_at=datetime.now() - timedelta(days=days_ago),
        source=source, entry_kind=kind, symbol=symbol, action=action,
        recommendation=action, entry_price=entry,
    ))


def _seed_quotes(s, symbol="600519.SH", *, n=25, start_days_ago=29, close=110.0):
    for i in range(n):
        d = date.today() - timedelta(days=start_days_ago - i)
        s.add(DailyQuote(symbol=symbol, market="a_share", date=d,
                         open=close, high=close, low=close, close=close, volume=1000))


# ── 1. 回填候选集 ────────────────────────────────────────────────────────────

def test_execution_and_ops_never_enter_backfill_candidates(db):
    """三类各一条、行情齐全 —— 只有 advice 被评。"""
    s = db()
    _seed(s, kind=ADVICE, symbol="600519.SH")
    _seed(s, kind=EXECUTION, symbol="600519.SH")
    _seed(s, kind=OPS, symbol="600519.SH")
    _seed_quotes(s)
    s.commit()
    s.close()

    stats = decision_log.backfill_outcomes()
    assert stats["total"] == 1, "回执/操作行不该出现在候选集里"
    assert stats["completed"] == 1

    s = db()
    for kind in (EXECUTION, OPS):
        row = s.query(DecisionLog).filter(DecisionLog.entry_kind == kind).one()
        assert row.outcome_status is None, f"{kind} 的行被评了：{row.outcome_status}"
        assert row.engine_version is None
    s.close()


def test_null_entry_kind_is_still_evaluated(db):
    """NULL 视同 advice —— 与 `normalize()` 同口径。

    宁可多评一条噪声，也不能把真建议**静默**排除在评估外：前者看得见，后者是
    「胜率悄悄少了几条样本」，永远不会有人发现。
    """
    s = db()
    _seed(s, kind=None, symbol="000001.SZ")
    _seed_quotes(s, "000001.SZ")
    s.commit()
    s.close()

    assert decision_log.backfill_outcomes()["completed"] == 1


# ── 2. 那颗具体的炸弹 ────────────────────────────────────────────────────────

def test_the_entry_100_btc_bomb_is_defused(db):
    """P0-4 的原始现场：`BTCUSDT.BN BUY entry=100`，后续 BTC 在 6.5 万。

    标成 execution 后**一根 bar 都不该被拿去算**。同时用一条对照行证明：
    若当成 advice，它就是一条天文数字的 win —— 也就是这张卡要防的那件事。

    ⚠️ **对照组用的是 entry=6500 而不是原始的 entry=100**（P0-4 批次2 起）：
    批次2 给 `outcome_eval` 加了离谱入场价守卫，entry=100 撞 65000 的 bar（650 倍）
    现在会被第二道防线判成 `unable/entry_price_outlier` —— 对照组会「因为被防住了」
    而失效，看起来像分类层没问题，其实什么都没证明。所以对照组降到 100 倍以内
    （6500 vs 65000 = 10 倍整，严格 `>` 不触发），**只考分类层这一道**。
    最后那段单独验第二道防线：即使 entry_kind 被误标成 advice，守卫也咬得住。
    """
    s = db()
    _seed(s, kind=EXECUTION, symbol="BTCUSDT.BN", entry=100.0, source="crypto")
    _seed_quotes(s, "BTCUSDT.BN", close=65000.0)
    s.commit()
    s.close()

    assert decision_log.backfill_outcomes()["total"] == 0

    s = db()
    row = s.query(DecisionLog).one()
    assert row.outcome_status is None
    assert row.return_20d is None, "回执行不该有收益率"
    s.close()

    # 对照：同样的数据当成 advice 会被判成天文数字的 win（entry 抬到守卫阈值内，
    # 单考分类层 —— 见上方 docstring 的说明）
    s = db()
    row = s.query(DecisionLog).one()
    row.entry_kind = ADVICE
    row.entry_price = 6500.0
    s.commit()
    s.close()
    decision_log.backfill_outcomes()
    s = db()
    row = s.query(DecisionLog).one()
    assert row.outcome_20d == "win" and row.return_20d > 800, (
        "对照组没复现出炸弹，说明这条测试已经测不到它要防的东西了"
    )
    s.close()


def test_the_bomb_has_a_second_line_of_defense(db):
    """**纵深防御**：分类层被绕过了，守卫还在（P0-4 批次2）。

    上一条测的是第一道防线（entry_kind 分类）。但分类是**写入点自觉**传的 ——
    将来某个新写入点忘了传、或者裸 SQL 灌进来一行，第一道就漏了。这一条把行**直接
    标成 advice**（模拟第一道失守），验第二道：`outcome_eval` 的离谱入场价守卫。

    两道防线的分工：分类层管「这行该不该被评」，守卫管「这个价能不能评」。
    """
    s = db()
    _seed(s, kind=ADVICE, symbol="BTCUSDT.BN", entry=100.0, source="crypto")
    _seed_quotes(s, "BTCUSDT.BN", close=65000.0)
    s.commit()
    s.close()

    stats = decision_log.backfill_outcomes()
    assert stats["total"] == 1 and stats["unable"] == 1

    s = db()
    row = s.query(DecisionLog).one()
    assert row.unable_reason == "entry_price_outlier"
    assert row.return_20d is None and row.outcome_20d is None, "一个数都不许算出来"
    s.close()


# ── 3. 胜率分母 ──────────────────────────────────────────────────────────────

def test_stats_denominator_excludes_execution_and_ops(db):
    """只堵回填不够 —— 回执的 outcome_status 恒 NULL，会被 total/pending 计进去。"""
    s = db()
    _seed(s, kind=ADVICE, symbol="600519.SH")
    for _ in range(5):
        _seed(s, kind=EXECUTION, symbol="BTCUSDT.BN", entry=100.0, source="crypto")
    _seed(s, kind=OPS, symbol="600519.SH", action=None)
    _seed_quotes(s)
    s.commit()
    s.close()
    decision_log.backfill_outcomes()

    stats = decision_log.get_decision_stats()
    assert stats["overall"]["total"] == 1, f"分母混进了回执/操作：{stats['overall']}"
    assert "crypto" not in stats["by_source"]

    # 显式要就能看到（审计线索一条不丢）
    all_kinds = decision_log.get_decision_stats(entry_kind=None)
    assert all_kinds["overall"]["total"] == 7
    assert decision_log.get_decision_stats(entry_kind=EXECUTION)["overall"]["total"] == 5


def test_query_defaults_to_advice_but_receipts_remain_queryable(db):
    s = db()
    _seed(s, kind=ADVICE, symbol="600519.SH")
    _seed(s, kind=EXECUTION, symbol="BTCUSDT.BN", source="crypto")
    s.commit()
    s.close()

    assert decision_log.query_decisions()["total"] == 1
    assert decision_log.query_decisions(entry_kind=None)["total"] == 2
    got = decision_log.query_decisions(entry_kind=EXECUTION)
    assert got["total"] == 1 and got["decisions"][0]["entry_kind"] == EXECUTION


def test_calibration_ignores_execution(db):
    """纵深防御：即使回执行**已经**带着 outcome 标签躺在库里（历史遗留），
    校准也不许拿它算 —— 校准失效是静默的，值得多一道 AND。"""
    s = db()
    for i in range(40):
        s.add(DecisionLog(
            decision_id=f"exec{i}", created_at=datetime.now() - timedelta(days=i + 1),
            source="crypto_cockpit", entry_kind=EXECUTION, symbol="BTCUSDT.BN",
            action="BUY", entry_price=100.0, confidence=90.0,
            outcome_20d="win", outcome_status="completed",
        ))
    s.commit()
    s.close()

    calib = decision_log.compute_calibration("crypto_cockpit")
    assert calib["total_samples"] == 0, "回执行混进了校准样本"
    assert calib["calibrated"] is False and calib["calibration_factor"] == 1.0


# ── 4. 分类表的完整性 ────────────────────────────────────────────────────────

def _confirmed_tool_names() -> set[str]:
    """源码里所有 `@tool(..., requires_confirmation=True)` 的工具名。

    ⚠️ **刻意不读运行期 `REGISTRY`**：`REGISTRY` 是全局单例，别的测试会往里塞
    `t_confirm` 这类假工具（`test_agent_safety.py` / `test_orchestrator_confirm_parallel.py`）
    —— 单跑绿、全套红。读源码 AST 拿到的是**代码的事实**，不受测试执行顺序影响，
    范式同 `tests/baseline/test_prompt_version_pinned.py` 的源码 grep 门禁。
    """
    import ast

    names: set[str] = set()
    tools_dir = os.path.join(_BACKEND, "agents", "tools")
    for fn in sorted(os.listdir(tools_dir)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(tools_dir, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            for deco in getattr(node, "decorator_list", []):
                if not (isinstance(deco, ast.Call) and getattr(deco.func, "id", "") == "tool"):
                    continue
                kw = {k.arg: k.value for k in deco.keywords}
                confirm = kw.get("requires_confirmation")
                if isinstance(confirm, ast.Constant) and confirm.value is True:
                    name = kw.get("name")
                    assert isinstance(name, ast.Constant), f"{fn} 里有个 @tool 的 name 不是字面量"
                    names.add(name.value)
    return names


def test_every_confirmed_tool_is_classified():
    """**新增一个受确认门的工具却忘了归类 → 这条红。**

    「靠默认值兜底」正是 P0-4 那颗炸弹的成因（crypto 回执默认跟 AI 建议同一类），
    所以这里要的是**显式覆盖**，不是运行期回落。
    """
    confirmed = _confirmed_tool_names()
    assert confirmed, "一个受确认门的工具都没扫到？AST 扫描八成失效了"
    missing = confirmed - set(CONFIRMED_TOOL_KINDS)
    assert not missing, (
        f"这些工具受确认门管、会落 DecisionLog，却没在 common/decision_kind.py 里归类："
        f"{sorted(missing)}。它是下单（execution）还是别的操作（ops）？"
    )
    stale = set(CONFIRMED_TOOL_KINDS) - confirmed
    assert not stale, f"这些工具已经不受确认门管了，请从分类表里删掉：{sorted(stale)}"
    assert set(CONFIRMED_TOOL_KINDS.values()) <= ENTRY_KINDS


def test_trade_tools_are_execution_not_ops():
    """真下单/补录成交必须是 execution —— 它们**有 action 也有成交价**，
    是这批里唯一会真的被评出胜负的，标错就等于把回执灌进胜率。"""
    for name in ("place_order", "place_crypto_order", "record_manual_trade"):
        assert kind_for_confirmed_tool(name) == EXECUTION
    for name in ("add_to_watchlist", "create_price_alert", "update_setting"):
        assert kind_for_confirmed_tool(name) == OPS
    assert kind_for_confirmed_tool("某个还没登记的新工具") == OPS, "未登记必须回落到 ops"


def test_normalize_falls_back_to_advice():
    assert normalize(None) == ADVICE and normalize("") == ADVICE
    assert normalize("EXECUTION") == EXECUTION and normalize("乱写的") == ADVICE
    assert EVALUABLE_KINDS == {ADVICE}


# ── 5. 写入点必须显式归类（源码 grep 门禁，范式抄 test_prompt_version_pinned.py）──

# 每个 record_decision 写入点 → 它写的是哪一类。**新增写入点必须进这张表**，
# 否则下一个「crypto 回执」又会靠默认值混进胜率分母。
_WRITE_SITES = {
    "advisor_engine/service.py": ADVICE,
    "report_engine/picks_log.py": ADVICE,
    "recommend_engine/engine.py": ADVICE,
    "agents/tools/analysis_tools.py": ADVICE,          # cockpit 评级
    "agents/tools/crypto_tools.py": ADVICE,            # crypto 驾驶舱评级
    "agents/confirm_gate.py": "per_tool",              # 运行期按工具名分流
    "crypto_intel_engine/execution.py": EXECUTION,     # 成交/挂单回执 + 理财申购(ops)
}


def test_no_unclassified_record_decision_write_site():
    """全项目的 `record_decision(` 调用点必须都在上表里。"""
    found = set()
    for root, _dirs, files in os.walk(_BACKEND):
        if any(p in root for p in (os.sep + "tests", os.sep + "__pycache__", os.sep + ".")):
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, _BACKEND)
            if rel == "decision_log.py":
                continue                                # 定义处，不是写入点
            src = open(path, encoding="utf-8").read()
            if re.search(r"\brecord_decision\(", src):
                found.add(rel)
    assert found == set(_WRITE_SITES), (
        f"决策留痕写入点变了。新增的：{sorted(found - set(_WRITE_SITES))}；"
        f"消失的：{sorted(set(_WRITE_SITES) - found)}。"
        f"新写入点请先想清楚：它写的是 AI 的可证伪断言（advice），还是已发生的"
        f"事实（execution/ops）？想清楚了再加进本表。"
    )


# ── 6. 存量数据的一次性归类（database.init_db 里的自动迁移）─────────────────

def test_migration_classifies_legacy_rows_and_never_overwrites(tmp_path, monkeypatch):
    """存量行（entry_kind IS NULL）按 source 归类，且**幂等 + 永不覆盖**。

    「永不覆盖」这一条是关键：迁移每次启动都跑，若它会覆盖已有值，那么将来某个
    写入点显式标的分类会在下次重启时被 SQL 的启发式推翻。
    """
    from data_engine.storage import database as db

    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(eng)
    make = sessionmaker(bind=eng)
    s = make()
    legacy = [
        ("crypto", "BUY", 100.0, EXECUTION),              # 成交/挂单回执
        ("crypto_earn", "SUBSCRIBE", None, OPS),          # 理财申购
        ("moneybill", "buy", 64200.0, EXECUTION),         # confirm_gate 的下单
        ("moneybill", None, None, OPS),                   # confirm_gate 的加自选股
        ("crypto_cockpit", "BUY", 66476.0, ADVICE),       # 币版驾驶舱评级
        ("report_picks", "BUY", 12.3, ADVICE),
        ("advisor", None, None, ADVICE),
    ]
    for i, (src, act, entry, _want) in enumerate(legacy):
        s.add(DecisionLog(decision_id=f"legacy{i}", source=src, entry_kind=None,
                          symbol="X", action=act, entry_price=entry))
    # 一条**已经**显式标好的行：迁移不许动它
    s.add(DecisionLog(decision_id="explicit", source="crypto", entry_kind=ADVICE,
                      symbol="X", action="BUY", entry_price=1.0))
    s.commit()
    s.close()
    # ⚠️ 必须用裸 SQL 把 legacy 行打回 NULL：ORM 的 `default="advice"` 对
    # `entry_kind=None` 也会生效（SQLAlchemy 把 None 当「没给值」），所以
    # 「存量行」这个状态在 ORM 里根本造不出来。
    with eng.begin() as conn:
        conn.execute(text("UPDATE decision_logs SET entry_kind = NULL "
                          "WHERE decision_id LIKE 'legacy%'"))

    monkeypatch.setattr(db, "engine", eng)
    db.init_db()
    db.init_db()          # 跑两遍：幂等

    s = make()
    for i, (src, _act, _entry, want) in enumerate(legacy):
        got = s.query(DecisionLog).filter(DecisionLog.decision_id == f"legacy{i}").one()
        assert got.entry_kind == want, f"source={src} 归类错了：{got.entry_kind} != {want}"
    kept = s.query(DecisionLog).filter(DecisionLog.decision_id == "explicit").one()
    assert kept.entry_kind == ADVICE, "迁移覆盖了已显式标好的分类"
    s.close()


@pytest.mark.parametrize("rel", [r for r, k in _WRITE_SITES.items() if k != ADVICE])
def test_non_advice_write_sites_pass_entry_kind_explicitly(rel):
    """非 advice 的写入点必须**显式**传 entry_kind —— 默认值不许兜底。"""
    src = open(os.path.join(_BACKEND, rel), encoding="utf-8").read()
    assert "entry_kind=" in src, (
        f"{rel} 写的不是 AI 建议，却没显式传 entry_kind —— "
        f"它会默认落成 advice 进胜率分母，正是 P0-4 那颗炸弹的形状。"
    )

"""交易终端的只读 route（S7）。

两条最要紧的：

1. 🔒 **只能有 GET** —— 所有写动作（改策略/arm/批准提案）必须继续走 MoneyBill 的
   确认门。这一组 route 是为了让终端「看得见」，不是给它开一条绕过确认的路。
2. **人话原样透传** —— `verdict` / `detail` / `means` 由引擎层产出，route 和前端
   都不许重写一套解释逻辑，否则口径立刻分叉。
"""
import asyncio

import pytest

from api.routes import arena
from data_engine.storage.database import get_session
from data_engine.storage.models import (
    CryptoArenaSwitch,
    CryptoStrategy,
    CryptoStrategyProposal,
    CryptoStrategyRun,
)

_SPEC = {
    "name": "测试策略",
    "universe": {"symbols": ["BTCUSDT.BN"]},
    "entry_rules": {"when": {"all_of": [{"field": "composite", "op": "gte", "value": 60}]}},
    "exit_rules": {"when": {"all_of": [{"field": "composite", "op": "lt", "value": 40}]}},
    "guardrails": {"per_order_notional_usdt": 100, "max_orders_per_day": 3},
}


class _Client:
    """直接 await handler，不经 TestClient。

    ⚠️ 仓库惯例（见 `tests/test_crypto_strategy_routes.py`）：route 测试不挂 TestClient
    —— 装的 httpx 与 starlette 的 TestClient 签名不兼容（`Client.__init__() got an
    unexpected keyword argument 'app'`），而这层耦合对「端点返回什么」毫无价值。
    """

    _HANDLERS = {
        "/arena/market-status": arena.market_status,
        "/arena/champion": arena.champion,
        "/arena/standings": arena.standings,
        "/arena/verdict": arena.verdict,
        "/arena/proposals": arena.proposals,
    }

    @staticmethod
    def get(path: str):
        base, _, qs = path.partition("?")
        fn = _Client._HANDLERS[base]
        kw = _defaults_of(fn)
        for pair in filter(None, qs.split("&")):
            k, _, v = pair.partition("=")
            kw[k] = {"true": True, "false": False}.get(v, v)
        for k in ("days", "limit"):
            if k in kw and kw[k] is not None:
                kw[k] = int(kw[k])
        return _Resp(asyncio.run(fn(**kw)))


def _defaults_of(fn) -> dict:
    """从 route 签名里**现算**默认值。

    ⚠️ 直接调 handler 时 FastAPI 不参与，`Query(30)` 这种默认值**不会被解析**
    （会原样把 `Query` 对象传进业务层 → `int(Query)` 炸）。

    ⛔ 早先这里是一张手抄的常量表，**会静默漂移**：把 route 的 `Query(30)` 改成
    `Query(7)`、表里仍写 30，全部测试照样绿 —— 从那一刻起测的是一个不存在的默认值。
    所以改成从签名里取 `Query` 对象的 `.default`。
    """
    import inspect
    out = {}
    for name, p in inspect.signature(fn).parameters.items():
        d = p.default
        out[name] = getattr(d, "default", d)   # Query 对象 → 它的 .default
    return out


class _Resp:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


@pytest.fixture
def client():
    return _Client()


@pytest.fixture
def clean():
    yield
    s = get_session()
    try:
        for m in (CryptoStrategyRun, CryptoStrategyProposal, CryptoArenaSwitch,
                  CryptoStrategy):
            s.query(m).delete()
        s.commit()
    finally:
        s.close()


def _mk(name="策略", **over):
    from crypto_intel_engine.dsl import CryptoStrategySpec
    from crypto_strategy.service import crypto_strategy_service as svc
    r = svc.compile_and_persist(CryptoStrategySpec(**{**_SPEC, "name": name}),
                                do_backtest=False)
    if over:
        s = get_session()
        try:
            row = s.query(CryptoStrategy).filter(
                CryptoStrategy.strategy_id == r["strategy_id"]).first()
            for k, v in over.items():
                setattr(row, k, v)
            s.commit()
        finally:
            s.close()
    return r["strategy_id"]


# ── 1. 🔒 只读 ───────────────────────────────────────────────────────────

def test_arena_router_exposes_only_get():
    """🔒 一条写端点都不许有。

    终端负责「看」，MoneyBill 负责「问」和「做」（裁决 17）。这一组 route 存在的
    理由是前端读不到 agent 工具，**不是**给它开一条绕过确认门的路。
    """
    from api.routes.arena import router
    methods = {m for r in router.routes for m in getattr(r, "methods", set())}
    assert methods <= {"GET", "HEAD", "OPTIONS"}, f"出现了写端点：{methods}"


def test_no_arena_endpoint_writes_to_the_database(client, clean):
    """🔒 五个端点跑一遍，**一条写 SQL 都不许有**。

    ⛔ 上一条门禁只看 HTTP 方法，挡不住「GET 里顺手写一笔」——
    实测过：往 `/arena/proposals` 里插一句 `backfill_outcomes()`（真写库），
    只看方法的那条测试**全绿**。终端是 60 秒轮询的，一个藏在 GET 里的写动作
    等于一分钟改一次库，而且没有任何人在看。

    所以直接在 engine 上挂 `before_execute` 数写语句。
    """
    from sqlalchemy import event

    from data_engine.storage.database import engine

    _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk("挑战者", enabled=1, mode="paper")

    writes: list[str] = []

    def _spy(conn, clauseelement, multiparams, params, execution_options):
        sql = str(clauseelement).lstrip().upper()
        if sql.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")):
            writes.append(sql.split("\n")[0][:120])

    event.listen(engine, "before_execute", _spy)
    try:
        for path in ("/arena/market-status", "/arena/champion", "/arena/standings",
                     "/arena/verdict", "/arena/proposals"):
            client.get(path)
    finally:
        event.remove(engine, "before_execute", _spy)

    assert not writes, f"只读端点写库了：{writes}"


def test_current_champion_agrees_with_evaluate_arena(clean):
    """便宜版「谁在跑」必须和全场判定选出同一条。

    `/champion` 刻意不跑 `evaluate_arena`（那会给每条 enabled 策略跑一遍回放），
    于是筛选条件被抄了第二份 —— 两份一旦分叉，卫冕者面板和竞技场判定就会
    指着不同的策略，而且**看不出来**。
    """
    from crypto_strategy.arena import current_champion, evaluate_arena

    assert current_champion() is None
    assert evaluate_arena()["champion"] is None

    sid = _mk("卫冕者", enabled=1, mode="live", status="armed")
    assert current_champion()["strategy_id"] == sid
    assert evaluate_arena()["champion"]["strategy_id"] == sid

    # 🔴 护栏熔断会把 enabled 置 0 —— 最该给判据的那一刻不能变成「没有卫冕者」
    s = get_session()
    try:
        row = s.query(CryptoStrategy).filter(CryptoStrategy.strategy_id == sid).first()
        row.enabled = 0
        row.status = "paused_by_guardrail"
        s.commit()
    finally:
        s.close()
    assert current_champion()["strategy_id"] == sid
    assert current_champion()["halted"] is True
    a = evaluate_arena()
    assert a["champion"]["strategy_id"] == sid
    assert a["champion_halted"] is True

    # 🔀 `market` 也是筛选条件之一（裁决 7 新读法：每个市场一条 live）——
    # 两个出口在**这一维**上分叉同样看不出来，所以一起钉在这条门禁里。
    stock = _mk("A股卫冕者", enabled=1, mode="live", status="armed", market="a_share")
    assert current_champion("a_share")["strategy_id"] == stock
    assert evaluate_arena(market="a_share")["champion"]["strategy_id"] == stock
    assert current_champion("crypto")["strategy_id"] == sid
    assert evaluate_arena(market="crypto")["champion"]["strategy_id"] == sid


def test_champion_does_not_replay_every_strategy(client, clean):
    """⭐ 卫冕者面板的成本必须是 O(1)，不是 O(策略数)。

    早先 `/champion` 调的是 `evaluate_arena`，而它会给**每条** enabled 策略跑一遍
    `daily_returns` → `cost_basis.replay`，可面板上只用得到 champion 那一条。
    终端 60 秒轮一次、四个端点齐发，这一份是纯白烧。

    ⚠️ `crypto_fills` 表**没有 `order_id` 索引**（建表只有 PK），`replay` 的
    `WHERE order_id IN (...)` 是全表扫 —— 成交攒到几万笔之后这件事会开始有感。

    ⚠️ 探针挂在 `daily_returns` 而不是 `cost_basis.replay`：库里没有成交时 replay
    压根不会被调到，拿它当计数器**这条测试就是空的**（试过，改坏了也绿）。
    `_snapshot` 里的 `daily_returns` 则是每条策略必调一次。
    """
    import crypto_strategy.performance as perf

    _mk("卫冕者", enabled=1, mode="live", status="armed")
    for i in range(5):
        _mk(f"挑战者{i}", enabled=1, mode="paper")

    calls = []
    orig = perf.daily_returns
    perf.daily_returns = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    try:
        client.get("/arena/champion")
    finally:
        perf.daily_returns = orig
    assert len(calls) <= 1, f"卫冕者面板算了 {len(calls)} 条日收益序列，应该只关心自己那条"


def test_arena_is_behind_auth():
    """与其它业务 route 同样受鉴权保护（不是公开端点）。"""
    import inspect

    from api import main
    src = inspect.getsource(main)
    assert "app.include_router(arena.router, dependencies=_auth)" in src


# ── 2. 各端点 ────────────────────────────────────────────────────────────

def test_market_status_lists_four_markets_and_crypto_is_always_open(client):
    """⭐ crypto 7×24 —— 别让它跟着 A 股一起显示休市（裁决 18）。"""
    r = client.get("/arena/market-status")
    assert r.status_code == 200
    markets = {m["market"]: m for m in r.json()["markets"]}
    assert set(markets) == {"a_share", "hk_stock", "us_stock", "crypto"}
    assert markets["crypto"]["is_open"] is True
    assert markets["crypto"]["label"] == "交易中"
    for m in markets.values():
        assert m["label"], "每个市场都要有人话标签"
        # ⛔ 别只给 ISO 串：前端一律 `new Date()` 转成**浏览器本地时区**，
        # 四个市场会显示成同一个钟点。当地几点必须后端算好。
        assert ":" in m["local_clock"]["wall_clock"]
    assert (markets["a_share"]["local_clock"]["utc_offset"]
            != markets["us_stock"]["local_clock"]["utc_offset"]), \
        "A 股和美股不可能是同一个 offset"


def test_champion_says_why_when_there_is_none(client, clean):
    """「没有卫冕者」不能只回一个空对象 —— 理由要原样带出来。"""
    r = client.get("/arena/champion")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "没有卫冕者" in body["reason"]


def test_champion_returns_health(client, clean):
    sid = _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk_run(sid)
    body = client.get("/arena/champion").json()
    assert body["strategy"]["strategy_id"] == sid
    assert body["ok"] is True
    assert "verdict" in body["health"]           # 人话结论原样透传


def test_champion_without_runs_still_names_the_strategy_and_has_no_runs_key(client, clean):
    """🔴 有卫冕者但窗口内**一条运行记录都没有** —— 前端崩过的就是这条路径。

    `strategy_health` 此时提前返回，**没有 `runs` 键**（也没有 pnl / orders_staged），
    但 `strategy` 是有的。前端只判「没有 strategy」就往下读 `health.runs.total`
    会直接 TypeError，而 ErrorBoundary 包的是整个 App —— 整个桌面 App 变错误页。

    触发场景很日常：刚 arm 一条新策略还没 tick；或引擎停了超过 `days` 天。
    """
    sid = _mk("刚武装还没跑", enabled=1, mode="live", status="armed")
    body = client.get("/arena/champion").json()
    assert body["strategy"]["strategy_id"] == sid   # 有卫冕者
    assert body["ok"] is False                      # 但算不出成绩
    assert "runs" not in body["health"], "这正是前端必须防的形状"
    assert body["reason"], "为什么算不出来，必须说"
    assert "一条运行记录都没有" in body["reason"]


def test_weakening_has_exactly_one_source(client, clean):
    """⭐「卫冕者正在变弱」只准从 `/verdict` 出。

    它的判定依赖窗口长度（`min_history=30`）：30 天窗口只给 31 个点、baseline 仅 17 天，
    跟 90 天窗口大概率给出**相反**结论。两块面板对同一件事各说各话，
    正是裁决 16「每个数字必须自解释」的反面。
    """
    sid = _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk_run(sid)
    champ = client.get("/arena/champion").json()
    assert "weakening" not in champ, "/champion 不许自己算变弱"
    assert "champion_weakening" in client.get("/arena/verdict").json()


def _mk_run(sid):
    from datetime import datetime
    s = get_session()
    try:
        s.add(CryptoStrategyRun(strategy_id=sid, status="evaluated", mode="live",
                                started_at=datetime.utcnow()))
        s.commit()
    finally:
        s.close()


def test_standings_puts_the_benchmark_first(client, clean):
    """⭐ 裁决 8：Jason 手写那条永久置顶 —— 没有基准线的胜率是自说自话。"""
    _mk("甲", enabled=1, mode="paper")
    bench = _mk("Jason 手写双均线", enabled=1, mode="paper", is_benchmark=1)
    _mk("乙", enabled=1, mode="paper")
    rows = client.get("/arena/standings").json()["strategies"]
    assert rows[0]["strategy_id"] == bench
    assert len(rows) == 3


def test_standings_hides_archived_by_default(client, clean):
    _mk("在跑的", enabled=1, mode="paper")
    _mk("退役的", enabled=0, status="retired")
    assert client.get("/arena/standings").json()["count"] == 1
    assert client.get(
        "/arena/standings?include_archived=true").json()["count"] == 2


def test_verdict_carries_the_gate_details(client, clean):
    sid = _mk("卫冕者", enabled=1, mode="live", status="armed")
    _mk("挑战者", enabled=1, mode="paper")
    body = client.get("/arena/verdict").json()
    assert body["champion"]["strategy_id"] == sid
    assert body["challengers"] == 1
    assert body["results"], "应该有逐个挑战者的判定"
    gates = body["results"][0]["gates"]
    assert all("detail" in g for g in gates.values()), "每道门槛都要有人话"
    # ⭐ 中文名也由后端出：前端抄过一份，措辞立刻分叉（「观察期够长」→「观察期」）
    assert all(g.get("label") for g in gates.values()), "每道门槛都要自带中文名"
    assert "不写库" in body["note"]


def test_verdict_challengers_is_always_an_int(client, clean):
    """有卫冕者时是 int、没有时也得是 int —— 前端 `len()` 或 `> 0` 不能炸。"""
    assert isinstance(client.get("/arena/verdict").json()["challengers"], int)
    _mk("卫冕者", enabled=1, mode="live", status="armed")
    assert isinstance(client.get("/arena/verdict").json()["challengers"], int)


def test_proposals_returns_diff_text_and_scorecard(client, clean):
    from crypto_intel_engine.dsl import CryptoStrategySpec
    from crypto_strategy import proposals as pr
    from crypto_strategy.service import crypto_strategy_service as svc

    base = svc.compile_and_persist(CryptoStrategySpec(**_SPEC), do_backtest=False)
    new = CryptoStrategySpec(**{**_SPEC, "interval_minutes": 15}).model_dump()
    pr.create_proposal(
        base_strategy_id=base["strategy_id"], family_id=base["summary"]["family_id"],
        shortfall="tick 太慢", change_summary="30→15", rationale="短波段走得快",
        expected_return_pct=0.08, expected_win_rate=0.55, horizon_days=30,
        new_spec=new, diff=pr.diff_specs(
            CryptoStrategySpec(**_SPEC).model_dump(), new))

    body = client.get("/arena/proposals").json()
    assert body["count"] == 1
    p = body["proposals"][0]
    assert "30 → 15" in p["diff_text"]              # 人话 diff，不是 JSON dump
    assert p["expected_return_pct"] == 0.08         # 可证伪断言
    assert body["scorecard"]["proposals"] == 1


def test_proposals_scorecard_never_leaks_a_percentage(client, clean):
    """🔴 一年 12-24 条提案，报百分比等于给噪声盖权威章。"""
    card = client.get("/arena/proposals").json()["scorecard"]
    assert "win_rate" not in card and "hit_rate" not in card
    assert "样本不足以下任何结论" in card["note"]

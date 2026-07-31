"""交易终端的**只读**数据出口（S7）。

# 为什么要有这组 route

S1~S4 的能力（策略战绩 / 竞技场判定 / 变更提案 / 提案后验）**全按铁律建在
agent 工具层**——「新能力只做引擎+工具两层，别再开页面/route」。代价是
**只有 AI 看得见它们**：前端页面读不到 agent 工具，后端也没有通用的工具执行端点。

于是交易终端的五块内容里有三块拿不到数据。Jason 2026-07-31 拍板补这一组。
铁律的本意是防「半用不用的页面满天飞」，而交易终端是他明确要的**第二块屏**。

# 🔒 这组 route 只开 GET

⛔ 不做通用工具执行端点：那等于开一扇很大的门 —— 写工具万一漏进白名单就绕过了
确认门；而且工具返回是给 LLM 的形状，前端还要另做一层适配。

⚠️ **别把这条读成「终端上没有写动作」**。终端页面上确实还有策略生命周期按钮
（arm / 暂停 / 退役 / 纸面干跑 / 回测），它们走的是 `crypto_strategy` 那组既有
route，不在本模块。Jason 2026-07-31 就此明确放行（见 `docs/15.策略竞技场/S7-交易终端.md`
的「实施偏离」）：**裁决 17 的「做」指的是 AI 的动作，Jason 自己点按钮本身就是人工审核**。
本模块只读，是因为这些是**新增**的查询能力，没有理由顺手开写口。

⚠️ 本模块**只做薄封装**，一行业务逻辑都不许写。人话结论（`verdict` / `detail` /
`means` / `label`）由引擎层产出，终端**原样展示** —— 在这里或前端重写一套解释逻辑，
会立刻和后端的口径分叉。

# ⚠️ async handler 里不许裸调同步 DB

`docs/GOTCHAS.md`「async 边界」：域 9 已把 `api/routes` 清零，**新增的别再破功**。
这组端点是 O(策略数) 的重活（每条策略要跑 `daily_returns` → `cost_basis.replay`），
在事件循环上直接跑会卡住 MoneyBill 的流式对话和 WS 推送。一律 `asyncio.to_thread`。

# ⚠️ 「卫冕者正在变弱」只有一个出口

`champion_weakening` 的判定**依赖窗口长度**（`min_history=30`），30 天窗口和 90 天
窗口大概率给出相反结论。所以本模块只让 `/verdict` 产出它，`/champion`
**故意不算** —— 同一块屏上对同一件事给两种说法，正是裁决 16 的反面。
"""
import asyncio
from typing import Any

from fastapi import APIRouter, Query
from loguru import logger

router = APIRouter(prefix="/arena", tags=["交易终端（只读）"])


@router.get("/champion")
async def champion(days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    """卫冕者面板：当前跑实盘那条的完整体检。

    ⚠️ 三种状态要分清，前端据此走三条不同的分支：
      1. `strategy` 为空 —— 压根没有在跑实盘的策略；
      2. `strategy` 有、`ok=False` —— 有卫冕者但算不出成绩（`reason` 说了为什么，
         最常见的是「窗口内一条运行记录都没有」），此时**没有 `health.runs`**；
      3. `ok=True` —— 正常。

    ⭐ **不调 `evaluate_arena`**：那会给每条 enabled 策略跑一遍回放，而这里只需要
    「谁在跑」+ 它自己的体检。「正在变弱」由 `/verdict` 出（见模块头）。
    """
    def _work() -> dict[str, Any]:
        from crypto_strategy.arena import current_champion
        from crypto_strategy.performance import strategy_health

        champ = current_champion()
        if not champ:
            return {"ok": False, "strategy": None,
                    "reason": ("现在没有在跑实盘的策略（没有卫冕者）——"
                               "「该不该换」这个问题还不成立。先 arm 一条上 live。")}
        h = strategy_health(champ["strategy_id"], days=days)
        return {
            "ok": bool(h.get("ok")),
            "strategy": champ,
            "halted": champ["halted"],
            "is_benchmark": champ["is_benchmark"],
            "health": h,
            "reason": None if h.get("ok") else h.get("reason"),
        }

    return await asyncio.to_thread(_work)


@router.get("/standings")
async def standings(days: int = Query(30, ge=1, le=365),
                    include_archived: bool = Query(False)) -> dict[str, Any]:
    """挑战者排行榜。

    ⛔ **刻意不排序**（`00-PLAN §3` 裁决 9 坑 3）：paper 与 live 不可比、样本量差异
    巨大，按收益排序等于诱导追涨杀跌。⭐ 但**基准线置顶**（裁决 8）——
    没有基准线的胜率是自说自话。
    """
    def _work() -> dict[str, Any]:
        from crypto_strategy.performance import standings as _standings
        rows = _standings(days=days, include_archived=include_archived)
        # 只有这一处排序：基准线永久置顶，其余保持引擎给的顺序（创建时间）。
        # ⚠️ `is_benchmark` 由 `standings()` 一次带出，别在这儿逐条回查（N+1）。
        rows.sort(key=lambda r: (not r.get("is_benchmark"),))
        return {"count": len(rows), "days": days, "strategies": rows,
                "ranking_note": "未排名——比较规则见 /arena/verdict 的四道门槛。"}

    return await asyncio.to_thread(_work)


@router.get("/verdict")
async def verdict(days: int = Query(90, ge=7, le=365)) -> dict[str, Any]:
    """竞技场判定：该不该换掉现在这条，卡在哪道门槛。

    ⚠️ 这是**规则的意见**，不写库、不改状态。真要换仍要走确认门。
    ⭐ `champion_weakening` 只从这里出（见模块头）。
    """
    def _work() -> dict[str, Any]:
        from crypto_strategy.arena import evaluate_arena
        return evaluate_arena(days=days)

    return await asyncio.to_thread(_work)


@router.get("/proposals")
async def proposals(limit: int = Query(10, ge=1, le=50),
                    status: str | None = Query(None)) -> dict[str, Any]:
    """AI 变更提案 + 战绩总账。

    ⚠️ 总账**只有计数没有胜率百分比**，这是刻意的：一年 12-24 条提案，统计显著要
    ≥30 样本，报百分比等于给噪声盖权威章（`00-PLAN §4` 问题 2）。
    """
    def _work() -> dict[str, Any]:
        from crypto_strategy import proposal_outcome as po
        from crypto_strategy import proposals as pr

        rows = pr.list_proposals(status=status, limit=limit)
        for r in rows:
            r["diff_text"] = pr.diff_text(r.get("diff") or [])
        try:
            card = po.scorecard()
        except Exception as e:  # noqa: BLE001 — 总账算不出来不该让待审列表也打不开
            logger.warning(f"提案战绩总账失败: {e}")
            card = None
        return {"count": len(rows), "proposals": rows, "scorecard": card}

    return await asyncio.to_thread(_work)


@router.get("/market-status")
async def market_status() -> dict[str, Any]:
    """四个市场现在是开着还是睡着（裁决 18）。

    ⭐ crypto 恒「交易中」—— 它 7×24，别让它跟着 A 股一起显示休市。
    """
    def _work() -> dict[str, Any]:
        from common.market import A_SHARE, CRYPTO, HK_STOCK, US_STOCK
        from common.market_session import describe, is_open, session_phase
        from common.market_time import market_now

        out = []
        for m in (A_SHARE, HK_STOCK, US_STOCK, CRYPTO):
            now = market_now(m)   # 交易所**本地时区**的 aware datetime
            out.append({
                "market": m,
                "phase": session_phase(m),
                "label": describe(m),
                "is_open": is_open(m),
                # ⛔ 别只吐一个 ISO 串：前端惯例是 `new Date()` + 格式化成**浏览器
                # 本地时区**，那样四个市场会显示成同一个钟点，"local_time" 这名字
                # 就成了骗人的。要「当地几点」就得给已经算好的墙上时间。
                "local_clock": {
                    "wall_clock": now.strftime("%H:%M"),
                    "date": now.strftime("%Y-%m-%d"),
                    "utc_offset": now.strftime("%z"),
                    "iso": now.isoformat(),
                },
            })
        return {"markets": out}

    return await asyncio.to_thread(_work)

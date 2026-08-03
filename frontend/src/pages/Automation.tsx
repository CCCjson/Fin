/**
 * 交易终端（S7）—— 仅次于 MoneyBill 的第二块屏。
 *
 * 裁决 17 原文是「终端负责『看』，MoneyBill 负责『问』和『做』」，S7 的验收里据此
 * 写过「终端上没有任何写动作」。⚠️ **Jason 2026-07-31 就这一条明确放行**：
 * 策略生命周期按钮（arm / 暂停 / 退役 / 纸面干跑 / 回测）**留在终端**。
 * 理由是那条永久约束管的是**AI 的动作**（「AI 只能提议，不能自行 arm」），
 * 而 Jason 自己点按钮**本身就是人工审核**。
 *
 * 所以准确的说法是：**这一页上没有 AI 能触发的写动作**。
 * ⛔ 别再把「终端零写动作」写进注释 —— 那句话在这份实现里是假的。
 * 🔒 真正没得商量的是 `agents/confirm_gate.py`：AI 走 `arm_crypto_strategy` 时
 * 仍然必须过确认门（那条路径还带 preview，会告诉你旧版本将被停掉）。
 *
 * 裁决 15：面板系统不写死布局，注册 widget 即出现（见 `terminal/PanelHost.tsx`）。
 * 裁决 16：**每个数字必须自解释** —— 人话结论全部来自后端引擎层，前端原样展示。
 * 裁决 18：顶栏一行显示四个市场「现在是开着还是睡着」。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCryptoStrategyStore } from '../stores/cryptoStrategyStore';
import { CryptoStrategyTab } from '../components/trading-center/CryptoStrategyTab';
import { CryptoPendingTab } from '../components/trading-center/CryptoPendingTab';
import { PanelHost, type PanelDef } from '../components/terminal/PanelHost';
import { MarketStatusBar } from '../components/terminal/MarketStatusBar';
import { MarketSwitcher } from '../components/terminal/MarketSwitcher';
import { ChampionPanel } from '../components/terminal/panels/ChampionPanel';
import { StandingsPanel } from '../components/terminal/panels/StandingsPanel';
import { ProposalsPanel } from '../components/terminal/panels/ProposalsPanel';
import {
  arenaService,
  type ArenaVerdict,
  type ChampionView,
  type ProposalRow,
  type ProposalScorecard,
  type StandingRow,
} from '../services/arenaService';

export const Automation: React.FC = () => {
  const store = useCryptoStrategyStore();
  const nav = useNavigate();

  const [champion, setChampion] = useState<ChampionView | null>(null);
  const [standings, setStandings] = useState<StandingRow[] | null>(null);
  const [standingsNote, setStandingsNote] = useState<string | undefined>();
  const [verdict, setVerdict] = useState<ArenaVerdict | null>(null);
  const [proposals, setProposals] = useState<ProposalRow[] | null>(null);
  const [scorecard, setScorecard] = useState<ProposalScorecard | null>(null);
  /**
   * 竞技场看哪个市场（S5 任务 03b）。裁决 7 新读法：每个市场同期一条 live，
   * 所以卫冕者/判定/排行榜三块都是**按市场**的。
   * ⚠️ 默认 crypto —— 与后端默认一致，也是唯一在跑真钱的市场。
   */
  const [market, setMarket] = useState('crypto');
  /**
   * 🔴 **当前市场的「真值」，给在途响应做判据。**
   *
   * 只把 `market` 加进依赖数组是**不够的**：那只修掉「定时器读到旧值」，
   * 修不掉**已经发出去的请求**。`/verdict` 走 `evaluate_arena`，要给每条策略跑
   * `daily_returns` + `cost_basis.replay`，是 O(策略数) 的重活 ——
   * 60 秒那次轮询以 crypto 发出、Jason 紧接着切到 A 股、A 股数据先回、
   * 两秒后 crypto 的响应落地把它整个盖掉。表现就是「切了，过一会儿又跳回去」，
   * 而面板上一个字段都不显示市场，屏幕上会是**顶着 A 股名字的 crypto 卫冕者**。
   * 来回切两下是很自然的操作，不是极端路径。
   */
  const marketRef = React.useRef(market);
  marketRef.current = market;

  const loadArena = useCallback((m: string) => {
    // ⚠️ 每个回调（含 catch）都要先验一次：旧市场的**失败**同样会把新市场的数据清掉。
    const fresh = () => m === marketRef.current;
    // 各自独立 catch：一块拿不到不该让整个终端空白
    arenaService.champion(30, m)
      .then((r) => { if (fresh()) setChampion(r); })
      .catch(() => { if (fresh()) setChampion(null); });
    // 🔴 排行榜**必须跟着一起切**：不传的话它是全市场混列的，
    //    于是「卫冕者」说的是 A 股、下面一列全是币策略，两块对不起来。
    arenaService.standings(30, false, m)
      .then((r) => {
        if (!fresh()) return;
        setStandings(r.strategies);
        // 空列表时这句话就是唯一的解释（含 fail-closed 的理由）
        setStandingsNote(r.ranking_note);
      })
      .catch(() => { if (fresh()) { setStandings([]); setStandingsNote(undefined); } });
    arenaService.verdict(90, m)
      .then((r) => { if (fresh()) setVerdict(r); })
      .catch(() => { if (fresh()) setVerdict(null); });
  }, []);

  // ⚠️ 提案**不分市场**（它挂在具体策略上），所以不进 `loadArena` ——
  //    跟着市场切会白算一遍 scorecard。
  const loadProposals = useCallback(() => {
    arenaService.proposals().then((r) => {
      setProposals(r.proposals);
      setScorecard(r.scorecard);
    }).catch(() => setProposals([]));
  }, []);

  useEffect(() => {
    store.refreshAll();
    store.connectWebSocket();
    loadProposals();
    return () => store.disconnectWebSocket();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 切市场要立刻重取（不然屏幕上是上一个市场的数字，而标题已经变了）
  useEffect(() => { loadArena(market); }, [market, loadArena]);

  // ⚠️ 待确认单/引擎状态的轮询**单独一个 effect**：跟市场绑在一起的话，
  //    每切一次市场这个计时器就被重置一次，而它盯的是「有真钱等着确认」。
  useEffect(() => {
    const t = setInterval(() => { store.fetchPending(); store.fetchEngine(); }, 20000);
    const tp = setInterval(loadProposals, 60000);
    return () => { clearInterval(t); clearInterval(tp); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // 战绩/判定是**慢变量**（按 tick 和成交推进），60 秒足够；
    // 跟待确认单一个频率纯属白打五个端点。
    const t2 = setInterval(() => loadArena(marketRef.current), 60000);
    return () => clearInterval(t2);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const eng = store.engine;
  const running = !!eng?.is_running;
  const killed = !!eng?.killed;
  const pendingCount = store.pending.length;

  const panels: PanelDef[] = useMemo(() => [
    {
      key: 'champion',
      title: '卫冕者',
      // ⚠️ ⛔ 别在这儿拼「没有卫冕者」那句结论：同一块面板的正文渲染的是后端
      //    `champion.reason`，两处措辞一分叉，屏上就会同时出现两种说法
      //    （实测：副标题「加密」、正文「crypto」指同一个市场）。
      //    市场是哪个由切换器回答，结论由后端那一句回答。
      subtitle: champion?.strategy
        ? `${champion.strategy.name} v${champion.strategy.version}`
        : undefined,
      // ⚠️ 「正在变弱」只认 `/verdict` 那一份 —— 它的判定依赖窗口长度，
      // 让 `/champion` 再算一份，同一块屏上就会对同一件事给出相反结论。
      badge: champion?.halted
        ? <span className="text-xs px-1.5 py-0.5 rounded bg-red-500/20 text-red-400">已熔断</span>
        : verdict?.champion_weakening?.weakening
          ? <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300">正在变弱</span>
          : null,
      render: () => <ChampionPanel data={champion} verdict={verdict} />,
    },
    {
      key: 'standings',
      title: '挑战者排行榜',
      subtitle: verdict ? `${verdict.challengers} 个挑战者` : undefined,
      render: () => <StandingsPanel rows={standings} verdict={verdict} note={standingsNote} />,
    },
    {
      key: 'proposals',
      title: 'AI 提案',
      badge: proposals?.some((p) => p.status === 'proposed')
        ? <span className="text-xs px-1.5 py-0.5 rounded bg-accent-purple/20 text-accent-purple animate-pulse">待审</span>
        : null,
      render: () => <ProposalsPanel rows={proposals} scorecard={scorecard} />,
    },
    {
      key: 'pending',
      title: '待确认单',
      pinned: true,   // 有真钱等着确认，不许被隐藏
      badge: pendingCount > 0
        ? <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-500 text-white animate-pulse">
            {pendingCount}
          </span>
        : null,
      render: () => <CryptoPendingTab />,
    },
    {
      key: 'strategies',
      title: '全部策略',
      subtitle: '参数、回测、生命周期',
      render: () => <CryptoStrategyTab />,
    },
  ], [champion, standings, standingsNote, verdict, proposals, scorecard, pendingCount]);

  return (
    <div className="h-screen flex flex-col bg-dark">
      {/* ===== 顶栏：市场状态 + 刹车（永远在同一个位置）===== */}
      <div className="flex-shrink-0 bg-dark-card border-b border-border">
        <div className="px-4 md:px-6 py-3 flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <span onClick={() => nav('/')} title="返回 MoneyBill"
              className="text-xl cursor-pointer hover:scale-110 transition-transform">💰</span>
            <h1 className="text-xl font-bold text-white">交易终端</h1>
            <MarketSwitcher
              value={market}
              options={champion?.markets_with_strategies ?? verdict?.markets_with_strategies}
              labels={champion?.market_labels}
              onChange={setMarket} />
            <MarketStatusBar />
          </div>

          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <div className={`w-2.5 h-2.5 rounded-full ${
                killed ? 'bg-red-600' : running ? 'bg-green-500 animate-pulse' : 'bg-gray-500'
              }`} />
              <span className="text-sm text-gray-400 hidden sm:inline">
                {killed ? '已急停' : running ? `引擎运行中(每${eng?.tick_min}分)` : '引擎已停'}
              </span>
            </div>

            <button
              onClick={() => running ? store.engineStop() : store.engineStart()}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                running ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                        : 'bg-green-500/20 text-green-400 hover:bg-green-500/30'
              }`}>
              {running ? '停止引擎' : '启动引擎'}
            </button>

            <button
              onClick={() => store.toggleKill()}
              title="总开关：急停后任何策略都不再产单"
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                killed ? 'bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30'
                       : 'bg-dark-light text-gray-400 hover:text-red-400 border border-border'
              }`}>
              {killed ? '解除急停' : '🛑 急停'}
            </button>

            {store.wsConnected && (
              <div className="flex items-center gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-green-500" />
                <span className="text-xs text-gray-500">WS</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ===== 面板区 ===== */}
      <div className="flex-1 overflow-auto p-3 md:p-6 pb-20 md:pb-6">
        <PanelHost panels={panels} />
      </div>
    </div>
  );
};

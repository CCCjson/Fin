/* ================================================================
   MoneyBill 趣味状态文案库 💬
   让等待过程「有性格」：思考 / 调工具 / 派 subagent 时显示俏皮文案。
   - 运行中：随机 + 定时轮换（useRotatingPhrase），多一点惊喜
   - 完成态：基于 tool_call id 哈希取稳定文案，刷新/重渲染不乱跳
   想加更多骚话，直接往下面的 map 里塞即可～
   ================================================================ */
import { useEffect, useRef, useState } from 'react';
import { useReducedMotion } from 'framer-motion';

// ---------------- 思考阶段（轮换） ----------------
export const THINKING_COPY = [
  '正在沉思🤔～请勿打扰❌',
  '脑子飞速运转中🌀…',
  '让我想想…🧠',
  '正在连接神经元⚡…',
  '酝酿措辞☕…',
  '掐指沉思💭…',
  '正在头脑风暴🌪️…',
  '思绪正在加载⏳…',
];

// ---------------- 工具「开工→收工」成对台词：同一次调用讲同一个梗 ----------------
export type Beat = { run: string; done: string };

export const TOOL_COPY: Record<string, Beat[]> = {
  search_stocks: [
    { run: '正在翻股票名册📖…', done: '找到这只票啦📇' },
    { run: '满世界找这只票🔍…', done: '逮到它了！🎯' },
    { run: '扒拉股票库🗂️…',   done: '从股票堆里挖出来了⛏️' },
  ],
  get_daily_data: [
    { run: '正在扒历史K线📉…', done: 'K线档案到手📂' },
    { run: '翻日线老黄历📅…',  done: '老黄历翻完，行情在此📜' },
    { run: '把K线一根根数过去🕯️…', done: '一根不落数完了🕯️' },
  ],
  get_realtime_quote: [
    { run: '正在贴脸盯盘👀…',   done: '实时价抓到了！💹' },
    { run: '掐着秒表看行情⏱️…', done: '行情快照拿下📸' },
  ],
  get_cockpit_score: [
    { run: '正在拿听诊器🩺…',   done: '体检报告出炉🧾' },
    { run: '给它做个全身CT🏥…', done: 'CT扫完，一切了然🫀' },
    { run: '量血压数心跳❤️…',   done: '各项指标记好啦📋' },
  ],
  predict_stock: [
    { run: '正在掐指一算🔮…',   done: '天机已算出✨' },
    { run: '摇一摇水晶球🪄…',   done: '水晶球显灵了🌟' },
    { run: '翻黄历看吉凶📜…',   done: '吉凶已卜📜' },
  ],
  get_positions: [
    { run: '正在数钱💰…',       done: '家底盘点完毕🏦' },
    { run: '翻翻你的钱包👛…',   done: '钱包看过了，都在这儿👛' },
  ],
  get_performance: [
    { run: '正在打算盘🧮…',     done: '账算清楚了🧮' },
    { run: '算算赚了还是亏了📊…', done: '成绩单来啦📊' },
  ],
  size_position: [
    { run: '正在拿尺子量仓位📏…', done: '仓位量好了📐' },
    { run: '掂量该下多少注🎰…',   done: '下注量拿捏好了🎯' },
    { run: '称称这一注的分量⚖️…', done: '分量称准了⚖️' },
  ],
  place_order: [
    { run: '正在填委托单📝…',   done: '委托单已递出📮' },
    { run: '下单前先深呼吸😤…', done: '单子送出去啦🚀' },
  ],
  // 知识库 & 联网
  search_knowledge: [
    { run: '正在翻金融大脑📚…', done: '脑子里挖到干货了🧠💡' },
    { run: '钻进内部图书馆📖…', done: '找到对应章节📑' },
    { run: '查内部小抄📔…',     done: '小抄上有答案📔' },
  ],
  web_search: [
    { run: '正在垃圾桶🗑️翻找垃圾～', done: '在垃圾桶里发现了宝藏！🪙' },  // Jason 钦点
    { run: '全网撒网捞消息🕸️…',   done: '网收上来啦，捞到几条大鱼🐟' },
    { run: '满互联网刨食🌐…',     done: '刨到料了，端给你🍖' },
  ],
  sec_search: [
    { run: '正在啃美股财报📄…', done: '财报啃完，要点摘好📌' },
    { run: '翻 SEC 档案柜🗄️…', done: '档案调出来了🗂️' },
  ],
  read_url: [
    { run: '正在逐字读网页👓…', done: '读完了，划好重点✍️' },
    { run: '啃这篇文章🍪…',     done: '嚼碎消化完毕😋' },
  ],
};

// ---------------- 兜底：未配置文案的工具 ----------------
export const FALLBACK_COPY: Beat[] = [
  { run: '正在寻找扳手🔧！！！', done: '扳手找到了，搞定🔧✅' },
  { run: '正在鼓捣中🛠️…',       done: '鼓捣好啦🛠️' },
  { run: '埋头干活🐝…',          done: '活儿干完🐝✅' },
];

// 失败统一兜底
export const TOOL_FAIL = '扑了个空，再试试…😵';

// ---------------- subagent 派单文案（handoff） ----------------
export const AGENT_RUNNING_COPY: Record<string, string[]> = {
  run_deep_stock:      ['派最强大脑深挖🧠…', '掘地三尺研究它⛏️…'],
  // 键必须是后端 subagent 的工具名。曾经写成 'Newnew'（那是数据监控页新闻定时任务
  // 的产品名），导致这条派单文案从来没被命中过，一直在走兜底。
  run_news_analysis:   ['满世界看新闻📰…', '吃瓜群众上线🍉…'],
  run_alpha_lab:       ['正在炼丹🔥…', '钻进实验室搞策略🧪…', '反复试错调参数🎛️…'],
  // 五个报告章节（13.2 把整篇周报拆开，可单调可组合）
  report_market:       ['正在盘大盘与板块🗺️…', '看资金往哪儿流💸…'],
  report_news:         ['正在读新闻挖情绪📰…', '扒政策与舆情🔍…'],
  report_positions:    ['正在给持仓做体检🩺…', '逐只翻你的票📋…'],
  report_strategy:     ['正在复盘上期推荐📈…', '算策略胜率🧮…'],
  report_picks:        ['正在海选买入标的🎯…', '多策略共振打分⚖️…'],
};

// ---------------- 工具函数 ----------------

/** 稳定哈希（djb2），同 seed 永远落到同一下标，避免重渲染/刷新后文案乱跳。 */
function hashSeed(seed: string): number {
  let h = 5381;
  for (let i = 0; i < seed.length; i++) h = ((h << 5) + h + seed.charCodeAt(i)) >>> 0;
  return h;
}

/** 基于 seed 取稳定的一项（用于完成态等需要稳定的场景）。 */
export function pickStable<T>(arr: T[], seed: string): T {
  return arr[hashSeed(seed) % arr.length];
}

/** 随机取一句。 */
export function pickRandom(arr: string[]): string {
  if (arr.length === 0) return '';
  return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * 轮换文案 hook：active 时每 intervalMs 随机切下一句（开场先随机一句）。
 * 尊重「减少动态效果」：reduce 时只取一句、不轮换。
 */
export function useRotatingPhrase(phrases: string[], active: boolean, intervalMs = 2800): string {
  const reduce = useReducedMotion();
  const idxRef = useRef<number>(Math.floor(Math.random() * Math.max(phrases.length, 1)));
  const [, force] = useState(0);

  useEffect(() => {
    if (!active || reduce || phrases.length <= 1) return;
    const t = setInterval(() => {
      // 随机跳到「非当前」的另一句，避免连续重复
      let next = Math.floor(Math.random() * phrases.length);
      if (next === idxRef.current) next = (next + 1) % phrases.length;
      idxRef.current = next;
      force((n) => n + 1);
    }, intervalMs);
    return () => clearInterval(t);
  }, [active, reduce, phrases.length, intervalMs]);

  if (phrases.length === 0) return '';
  return phrases[idxRef.current % phrases.length];
}

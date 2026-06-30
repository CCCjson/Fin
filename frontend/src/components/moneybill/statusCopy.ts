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

// ---------------- 工具运行中文案：每个工具一组 ----------------
export const TOOL_RUNNING_COPY: Record<string, string[]> = {
  search_stocks:      ['正在翻股票名册📖…', '满世界找这只票🔍…', '扒拉股票库🗂️…'],
  get_daily_data:     ['正在扒历史K线📉…', '翻日线老黄历📅…', '把K线一根根数过去🕯️…'],
  get_realtime_quote: ['正在贴脸盯盘👀…', '掐着秒表看行情⏱️…', '实时价我盯死了💹…'],
  get_cockpit_score:  ['正在拿听诊器🩺…', '给它做个全身CT🏥…', '量血压数心跳❤️…'],
  predict_stock:      ['正在掐指一算🔮…', '摇一摇水晶球🪄…', '翻黄历看吉凶📜…'],
  get_positions:      ['正在数钱💰…', '翻翻你的钱包👛…', '清点家底🏦…'],
  get_performance:    ['正在打算盘🧮…', '算算赚了还是亏了📊…', '结算战绩🏆…'],
  size_position:      ['正在拿尺子量仓位📏…', '掂量该下多少注🎰…', '称称这一注的分量⚖️…'],
  place_order:        ['正在填委托单📝…', '下单前先深呼吸😤…'],
  // 知识库 & 联网
  search_knowledge:   ['正在翻金融大脑📚…', '钻进内部图书馆📖…', '查内部小抄📔…'],
  web_search:         ['正在垃圾桶🗑️翻找垃圾～', '全网撒网捞消息🕸️…', '满互联网刨食🌐…'],
  sec_search:         ['正在啃美股财报📄…', '翻 SEC 档案柜🗄️…'],
  read_url:           ['正在逐字读网页👓…', '啃这篇文章🍪…'],
};

// ---------------- subagent 派单文案（handoff） ----------------
export const AGENT_RUNNING_COPY: Record<string, string[]> = {
  run_deep_stock:      ['派最强大脑深挖🧠…', '掘地三尺研究它⛏️…'],
  run_news_analysis:   ['满世界看新闻📰…', '吃瓜群众上线🍉…'],
  run_research_report: ['正在爆肝写报告✍️…', '熬夜码研报☕…'],
  run_alpha_lab:       ['正在炼丹🔥…', '钻进实验室搞策略🧪…', '反复试错调参数🎛️…'],
};

// ---------------- 完成态短标签（保留 ✓/✕；其余沿用 TOOL_LABELS） ----------------
export const TOOL_DONE_LABEL: Record<string, string> = {
  search_stocks: '搜完了',
  get_daily_data: '日线到手',
  get_realtime_quote: '行情已看',
  get_cockpit_score: '体检完毕',
  predict_stock: '算好了',
  get_positions: '清点完毕',
  get_performance: '战绩结算',
  size_position: '量好仓位',
  place_order: '委托已提',
  search_knowledge: '查到啦',
  web_search: '翻完啦🗑️',
  sec_search: '财报啃完',
  read_url: '读完了',
};

// ---------------- 兜底：未配置文案的工具 ----------------
export const FALLBACK_RUNNING = ['正在寻找扳手🔧！！！', '正在鼓捣中🛠️…', '埋头干活🐝…'];

// ---------------- 工具函数 ----------------

/** 稳定哈希（djb2），同 seed 永远落到同一下标，避免重渲染/刷新后文案乱跳。 */
function hashSeed(seed: string): number {
  let h = 5381;
  for (let i = 0; i < seed.length; i++) h = ((h << 5) + h + seed.charCodeAt(i)) >>> 0;
  return h;
}

/** 基于 seed 取稳定的一句（用于完成态等需要稳定的场景）。 */
export function pickStable(arr: string[], seed: string): string {
  if (arr.length === 0) return '';
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

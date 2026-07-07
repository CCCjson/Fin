import type { PageContext } from '../services/agentService';
import { usePageContextStore } from '../store/pageContextStore';

/* ================================================================
   capturePageContext —— 发送消息时采集「当前屏幕在看什么」
   1. route  → 友好中文页名
   2. entities → 关键页面写入的对象（如 { symbol }），路由匹配才用
   3. visible_text → 当前可见页面的纯文本（文字版截图），封顶 3000 字
   浮窗是 fixed 挂在内容区之外，天然不会把自己抓进来。
   ================================================================ */

const ROUTE_NAMES: Record<string, string> = {
  '/': 'MoneyBill 对话',
  '/app': 'MoneyBill 对话',
  '/app/market': '行情',
  '/app/backtest': '策略回测',
  '/app/orderbook': '盘口深度',
  '/app/prediction': '涨跌预测',
  '/app/data-monitor': '数据监控',
  '/trading': '自动化交易',
  '/fine-tune': '模型微调',
};

const VISIBLE_TEXT_MAX = 3000;

/** 给表单控件找个可读的 label：aria-label → 关联 label → placeholder → name。 */
function fieldLabel(el: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement): string {
  const aria = el.getAttribute('aria-label');
  if (aria) return aria.trim();
  const labels = (el as HTMLInputElement).labels;
  if (labels && labels.length && labels[0].innerText.trim()) return labels[0].innerText.trim();
  const ph = (el as HTMLInputElement).placeholder;
  if (ph) return ph.trim();
  if (el.name) return el.name;
  return '输入';
}

/** innerText 抓不到表单控件的 value，这里单独收集（如股票代码输入框里的 688576.SH）。 */
function grabFieldValues(root: HTMLElement): string {
  const els = root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(
    'input, textarea, select',
  );
  const lines: string[] = [];
  els.forEach((el) => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'hidden' || type === 'password') return;
    let value = '';
    if (el.tagName === 'SELECT') {
      value = (el as HTMLSelectElement).selectedOptions?.[0]?.text?.trim() || '';
    } else if (type === 'checkbox' || type === 'radio') {
      if (!(el as HTMLInputElement).checked) return;
      value = '已选';
    } else {
      value = (el as HTMLInputElement | HTMLTextAreaElement).value?.trim() || '';
    }
    if (!value) return;
    lines.push(`${fieldLabel(el)}: ${value}`);
  });
  return lines.join('\n');
}

/** 抓当前可见页面的文本：优先 data-page-visible=true 节点，避免保活隐藏页混入。 */
function grabVisibleText(): string {
  if (typeof document === 'undefined') return '';
  const active = document.querySelector<HTMLElement>('[data-page-visible="true"]');
  const root = active ?? document.querySelector<HTMLElement>('#main-stage') ?? document.body;
  if (!root) return '';
  const text = (root.innerText ?? '').replace(/\s+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim();
  const fields = grabFieldValues(root);
  const combined = fields ? `${text}\n\n【当前输入/筛选】\n${fields}` : text;
  return combined.slice(0, VISIBLE_TEXT_MAX);
}

export function capturePageContext(): PageContext {
  const path = typeof window !== 'undefined' ? window.location.pathname : '/';
  const page = ROUTE_NAMES[path] ?? '未知页面';

  const pcs = usePageContextStore.getState();
  const entities = pcs.path === path ? pcs.entities : {};

  return {
    page,
    path,
    entities,
    visible_text: grabVisibleText(),
  };
}

/**
 * 后端时间字段 → 浏览器本地时间的统一渲染。
 *
 * ## 为什么必须收口到这里
 *
 * 后端正在把所有 `DateTime` 列翻转成 **naive UTC** 存储、序列化时带显式 offset
 * （`2026-07-22T02:24:53+00:00`，见 backend/common/market_time.py 的 `utc_iso`）。
 * 带 offset 之后，只要走**真正的日期解析**（`new Date(iso)`）就会自动转成浏览器
 * 本地时区，显示不变；但**把时间当字符串切**的写法（`.slice(5, 16)`、
 * `.split('T')[0]`、直接 `{someTime}` 渲染）会原样吐出 UTC 时刻，凭空差 8 小时。
 *
 * 所以：**任何时间字段一律走本模块，不许再字符串切片。**
 *
 * ## 为什么自己规整字符串而不是直接 new Date()
 *
 * 桌面 App 是 Tauri 的 WKWebView（JavaScriptCore），不是 Chrome 的 V8。两个引擎对
 * 非标准 ISO 的容忍度不同 —— 后端历史上存在三种形态并存：
 *
 *   `2026-07-22 10:24:53.123456`  ← Python `str(datetime)`：空格分隔 + 6 位微秒
 *   `2026-07-22T10:24:53`         ← FastAPI 默认（naive，无 offset）
 *   `2026-07-22T02:24:53+00:00`   ← 翻转后的目标形态
 *
 * 这里先把前两种规整成标准 ISO 再交给引擎，避免「Chrome 上好好的、装进 App 就
 * Invalid Date」这类只在桌面端复现的问题。
 *
 * ## 纯日期串不要过 Date
 *
 * `new Date('2026-07-22')` 按 ECMA 规范是 **UTC 零点**，在 UTC+8 渲染回来会变成
 * 07-22 08:00，往前推的时区甚至会退成 07-21。交易日（`daily_quotes.date` /
 * `signals.date` / `trade_date` 这类 `Column(Date)`）**不参与 UTC 翻转**，直接原样显示。
 */
import { format } from 'date-fns';

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** 规整成引擎都认的 ISO：空格→T，微秒截到毫秒。纯日期串返回 null（调用方原样显示）。 */
function toDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
  if (typeof value === 'number') {
    const d = new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }
  const raw = value.trim();
  if (!raw || DATE_ONLY.test(raw)) return null;
  const normalized = raw
    .replace(' ', 'T')
    .replace(/\.(\d{3})\d+/, '.$1');   // 6 位微秒 → 3 位毫秒
  const d = new Date(normalized);
  return isNaN(d.getTime()) ? null : d;
}

function render(value: string | number | Date | null | undefined,
                pattern: string, fallback: string): string {
  const d = toDate(value);
  if (d) return format(d, pattern);
  // 纯日期串（交易日）原样显示；解析不了的也原样吐出去，不要吞成空白
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

/** `07-22 10:24` —— 列表/卡片里的紧凑时刻（本地时区）。 */
export function localDateTime(value: string | number | Date | null | undefined,
                             fallback = '—'): string {
  return render(value, 'MM-dd HH:mm', fallback);
}

/** `2026-07-22 10:24:53` —— 需要完整时刻时用（本地时区）。 */
export function localFullDateTime(value: string | number | Date | null | undefined,
                                  fallback = '—'): string {
  return render(value, 'yyyy-MM-dd HH:mm:ss', fallback);
}

/** `10:24` —— 只关心几点（本地时区）。 */
export function localTime(value: string | number | Date | null | undefined,
                          fallback = '—'): string {
  return render(value, 'HH:mm', fallback);
}

/** `2026-07-22` —— 时刻按本地时区落到「哪一天」。纯日期串原样返回。 */
export function localDate(value: string | number | Date | null | undefined,
                          fallback = '—'): string {
  return render(value, 'yyyy-MM-dd', fallback);
}

/** 距今多久（`3 分钟前` / `2 小时前` / `5 天前`）。未来时刻返回 `即将`。 */
export function timeAgo(value: string | number | Date | null | undefined,
                        fallback = '—'): string {
  const d = toDate(value);
  if (!d) return fallback;
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 0) return '即将';
  if (sec < 60) return '刚刚';
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return `${Math.floor(sec / 86400)} 天前`;
}

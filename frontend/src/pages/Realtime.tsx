import React, { useState, useCallback, useMemo, useRef } from 'react';
import type {
  RealtimeQuote,
  MarketStatistics,
  IndexData,
  RealtimeStreamEvent,
} from '../services/realtimeService';
import realtimeService from '../services/realtimeService';

// 格式化成交额（万/亿）
function formatAmount(val: number | null): string {
  if (val === null || val === undefined) return '-';
  if (val >= 1e8) return (val / 1e8).toFixed(2) + '亿';
  if (val >= 1e4) return (val / 1e4).toFixed(1) + '万';
  return val.toFixed(0);
}

// 格式化成交量（万手）
function formatVolume(val: number | null): string {
  if (val === null || val === undefined) return '-';
  if (val >= 10000) return (val / 10000).toFixed(1) + '万';
  return val.toFixed(0);
}

// 涨跌颜色
function changeColor(val: number | null): string {
  if (val === null || val === undefined) return 'text-gray-400';
  if (val > 0) return 'text-red-500';
  if (val < 0) return 'text-green-500';
  return 'text-gray-400';
}

// 板块筛选
type BoardFilter = 'all' | 'sh_main' | 'sz_main' | 'gem' | 'star';

function matchBoard(q: RealtimeQuote, board: BoardFilter): boolean {
  const code = q.code || '';
  switch (board) {
    case 'sh_main': return code.startsWith('6');
    case 'sz_main': return code.startsWith('00');
    case 'gem': return code.startsWith('3');
    case 'star': return code.startsWith('68');
    default: return true;
  }
}

const BOARD_OPTIONS: { value: BoardFilter; label: string }[] = [
  { value: 'all', label: '全部' },
  { value: 'sh_main', label: '沪市主板' },
  { value: 'sz_main', label: '深市主板' },
  { value: 'gem', label: '创业板' },
  { value: 'star', label: '科创板' },
];

const SORT_OPTIONS = [
  { value: 'change_pct', label: '涨跌幅' },
  { value: 'amount', label: '成交额' },
  { value: 'turnover', label: '换手率' },
  { value: 'volume', label: '成交量' },
];

// 每页显示条数
const PAGE_SIZE = 100;

// sessionStorage 持久化 + 模块级缓存
const STORAGE_KEY = 'realtime_cache';

function loadCache() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return null;
}

function saveCache(data: { quotes: RealtimeQuote[]; statistics: MarketStatistics | null; indices: IndexData[]; updateTime: string }) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch { /* ignore, e.g. quota exceeded */ }
}

const _initial = loadCache();
let cachedQuotes: RealtimeQuote[] = _initial?.quotes || [];
let cachedStatistics: MarketStatistics | null = _initial?.statistics || null;
let cachedIndices: IndexData[] = _initial?.indices || [];
let cachedUpdateTime: string = _initial?.updateTime || '';

export const Realtime: React.FC = () => {
  const [quotes, setQuotes] = useState<RealtimeQuote[]>(cachedQuotes);
  const [statistics, setStatistics] = useState<MarketStatistics | null>(cachedStatistics);
  const [indices, setIndices] = useState<IndexData[]>(cachedIndices);
  const [loading, setLoading] = useState(false);
  const [updateTime, setUpdateTime] = useState<string>(cachedUpdateTime);
  const [useProxy, setUseProxy] = useState(false);

  // 进度状态
  const [progress, setProgress] = useState<{ percent: number; fetched: number; total: number; status: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // 筛选/排序状态
  const [search, setSearch] = useState('');
  const [board, setBoard] = useState<BoardFilter>('all');
  const [sortBy, setSortBy] = useState('change_pct');
  const [ascending, setAscending] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);

  // 加载数据（流式 + 进度条）
  const fetchData = useCallback(async () => {
    // 取消上一次请求
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setProgress({ percent: 0, fetched: 0, total: 0, status: '正在连接...' });

    try {
      // 指数数据独立请求（不需要流式）
      realtimeService.getIndices({ use_proxy: useProxy }).then(res => {
        if (res.success) {
          cachedIndices = res.data;
          setIndices(cachedIndices);
          saveCache({ quotes: cachedQuotes, statistics: cachedStatistics, indices: cachedIndices, updateTime: cachedUpdateTime });
        }
      }).catch(() => {});

      // 流式获取行情
      await realtimeService.streamQuotes(
        { sort_by: sortBy, ascending, save: true },
        (event: RealtimeStreamEvent) => {
          switch (event.event) {
            case 'progress':
              setProgress({
                percent: event.percent || 0,
                fetched: event.fetched || 0,
                total: event.total || 0,
                status: `正在获取行情 ${event.fetched}/${event.total}`,
              });
              break;
            case 'saving':
              setProgress(prev => prev ? { ...prev, percent: 100, status: '正在保存到数据库...' } : null);
              break;
            case 'done':
              if (event.data) {
                cachedQuotes = event.data;
                cachedStatistics = event.statistics || null;
                cachedUpdateTime = new Date().toLocaleTimeString('zh-CN');
                setQuotes(cachedQuotes);
                setStatistics(cachedStatistics);
                setUpdateTime(cachedUpdateTime);
                setCurrentPage(1);
                saveCache({ quotes: cachedQuotes, statistics: cachedStatistics, indices: cachedIndices, updateTime: cachedUpdateTime });
              }
              setProgress(null);
              setLoading(false);
              break;
            case 'error':
              console.error('行情获取失败:', event.message);
              setProgress(null);
              setLoading(false);
              break;
          }
        },
        controller.signal,
      );
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.error('获取行情失败:', err);
      }
      setProgress(null);
      setLoading(false);
    }
  }, [useProxy, sortBy, ascending]);

  // 前端过滤
  const filteredQuotes = useMemo(() => {
    let result = quotes;

    // 板块筛选
    if (board !== 'all') {
      result = result.filter(q => matchBoard(q, board));
    }

    // 搜索
    if (search.trim()) {
      const keyword = search.trim().toLowerCase();
      result = result.filter(
        q =>
          (q.code && q.code.includes(keyword)) ||
          (q.name && q.name.toLowerCase().includes(keyword))
      );
    }

    return result;
  }, [quotes, board, search]);

  // 分页
  const totalPages = Math.ceil(filteredQuotes.length / PAGE_SIZE);
  const pagedQuotes = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return filteredQuotes.slice(start, start + PAGE_SIZE);
  }, [filteredQuotes, currentPage]);

  return (
    <div className="p-6 space-y-4">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">实时行情</h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-400">
            <input
              type="checkbox"
              checked={useProxy}
              onChange={e => setUseProxy(e.target.checked)}
              className="rounded bg-dark-card border-border"
            />
            使用代理 IP
          </label>
          <button
            onClick={fetchData}
            disabled={loading}
            className="px-5 py-2 bg-primary hover:bg-primary-dark text-white rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            {loading ? '获取中...' : '获取行情'}
          </button>
          {updateTime && (
            <span className="text-xs text-gray-500">更新于 {updateTime}</span>
          )}
        </div>
      </div>

      {/* 进度条 */}
      {progress && (
        <div className="bg-dark-card rounded-xl p-4 border border-border">
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm text-gray-300">{progress.status}</span>
            <span className="text-sm text-primary font-mono">{progress.percent}%</span>
          </div>
          <div className="w-full bg-dark rounded-full h-2.5 overflow-hidden">
            <div
              className="h-full bg-gradient-to-r from-primary to-blue-400 rounded-full transition-all duration-300 ease-out"
              style={{ width: `${progress.percent}%` }}
            />
          </div>
          {progress.total > 0 && (
            <div className="text-xs text-gray-500 mt-1.5">
              已获取 {progress.fetched} / {progress.total} 只股票
            </div>
          )}
        </div>
      )}

      {/* 大盘指数 */}
      {indices.length > 0 && (
        <div className="grid grid-cols-4 gap-4">
          {indices.map(idx => (
            <div key={idx.code} className="bg-dark-card rounded-xl p-4 border border-border">
              <div className="text-sm text-gray-400 mb-1">{idx.name}</div>
              <div className={`text-2xl font-bold ${changeColor(idx.change_pct)}`}>
                {idx.price?.toFixed(2) ?? '-'}
              </div>
              <div className="flex gap-3 mt-1 text-sm">
                <span className={changeColor(idx.change_pct)}>
                  {idx.change_pct != null ? (idx.change_pct > 0 ? '+' : '') + idx.change_pct.toFixed(2) + '%' : '-'}
                </span>
                <span className={changeColor(idx.change_amount)}>
                  {idx.change_amount != null ? (idx.change_amount > 0 ? '+' : '') + idx.change_amount.toFixed(2) : '-'}
                </span>
                <span className="text-gray-500">
                  {formatAmount(idx.amount)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 市场统计卡片 */}
      {statistics && (
        <div className="grid grid-cols-5 gap-4">
          <div className="bg-dark-card rounded-xl p-4 border border-border text-center">
            <div className="text-sm text-gray-400">上涨</div>
            <div className="text-2xl font-bold text-red-500">{statistics.up}</div>
          </div>
          <div className="bg-dark-card rounded-xl p-4 border border-border text-center">
            <div className="text-sm text-gray-400">下跌</div>
            <div className="text-2xl font-bold text-green-500">{statistics.down}</div>
          </div>
          <div className="bg-dark-card rounded-xl p-4 border border-border text-center">
            <div className="text-sm text-gray-400">平盘</div>
            <div className="text-2xl font-bold text-gray-400">{statistics.flat}</div>
          </div>
          <div className="bg-dark-card rounded-xl p-4 border border-border text-center">
            <div className="text-sm text-gray-400">涨停</div>
            <div className="text-2xl font-bold text-red-400">{statistics.limit_up}</div>
          </div>
          <div className="bg-dark-card rounded-xl p-4 border border-border text-center">
            <div className="text-sm text-gray-400">跌停</div>
            <div className="text-2xl font-bold text-green-400">{statistics.limit_down}</div>
          </div>
        </div>
      )}

      {/* 筛选工具栏 */}
      {quotes.length > 0 && (
        <div className="flex items-center gap-4 bg-dark-card rounded-xl p-3 border border-border">
          {/* 搜索 */}
          <input
            type="text"
            placeholder="搜索代码或名称..."
            value={search}
            onChange={e => { setSearch(e.target.value); setCurrentPage(1); }}
            className="px-3 py-2 bg-dark border border-border rounded-lg text-white text-sm w-48 focus:outline-none focus:border-primary"
          />

          {/* 板块 */}
          <div className="flex gap-1">
            {BOARD_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => { setBoard(opt.value); setCurrentPage(1); }}
                className={`px-3 py-1.5 text-xs rounded-lg transition-colors ${
                  board === opt.value
                    ? 'bg-primary text-white'
                    : 'text-gray-400 hover:bg-dark-light hover:text-white'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 排序 */}
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-gray-500">排序:</span>
            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value)}
              className="px-2 py-1.5 bg-dark border border-border rounded-lg text-white text-xs focus:outline-none"
            >
              {SORT_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <button
              onClick={() => setAscending(!ascending)}
              className="px-2 py-1.5 bg-dark border border-border rounded-lg text-xs text-gray-400 hover:text-white"
            >
              {ascending ? '升序' : '降序'}
            </button>
          </div>

          <span className="text-xs text-gray-500">
            共 {filteredQuotes.length} 只
          </span>
        </div>
      )}

      {/* 行情列表 */}
      {quotes.length > 0 && (
        <div className="bg-dark-card rounded-xl border border-border overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-gray-400 text-xs">
                  <th className="px-4 py-3 text-left font-medium">代码</th>
                  <th className="px-4 py-3 text-left font-medium">名称</th>
                  <th className="px-4 py-3 text-right font-medium">最新价</th>
                  <th className="px-4 py-3 text-right font-medium">涨跌幅</th>
                  <th className="px-4 py-3 text-right font-medium">涨跌额</th>
                  <th className="px-4 py-3 text-right font-medium">成交量(手)</th>
                  <th className="px-4 py-3 text-right font-medium">成交额</th>
                  <th className="px-4 py-3 text-right font-medium">振幅</th>
                  <th className="px-4 py-3 text-right font-medium">换手率</th>
                  <th className="px-4 py-3 text-right font-medium">最高</th>
                  <th className="px-4 py-3 text-right font-medium">最低</th>
                  <th className="px-4 py-3 text-right font-medium">今开</th>
                  <th className="px-4 py-3 text-right font-medium">昨收</th>
                </tr>
              </thead>
              <tbody>
                {pagedQuotes.map((q, i) => (
                  <tr
                    key={q.symbol || i}
                    className="border-b border-border/50 hover:bg-dark-light/50 transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-2.5 text-primary-light font-mono">{q.code}</td>
                    <td className="px-4 py-2.5 text-white">{q.name}</td>
                    <td className={`px-4 py-2.5 text-right font-medium ${changeColor(q.change_pct)}`}>
                      {q.price?.toFixed(2) ?? '-'}
                    </td>
                    <td className={`px-4 py-2.5 text-right font-medium ${changeColor(q.change_pct)}`}>
                      {q.change_pct != null ? (q.change_pct > 0 ? '+' : '') + q.change_pct.toFixed(2) + '%' : '-'}
                    </td>
                    <td className={`px-4 py-2.5 text-right ${changeColor(q.change_amount)}`}>
                      {q.change_amount != null ? (q.change_amount > 0 ? '+' : '') + q.change_amount.toFixed(2) : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-300">{formatVolume(q.volume)}</td>
                    <td className="px-4 py-2.5 text-right text-gray-300">{formatAmount(q.amount)}</td>
                    <td className="px-4 py-2.5 text-right text-gray-400">
                      {q.amplitude != null ? q.amplitude.toFixed(2) + '%' : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-yellow-400">
                      {q.turnover != null ? q.turnover.toFixed(2) + '%' : '-'}
                    </td>
                    <td className="px-4 py-2.5 text-right text-gray-300">{q.high?.toFixed(2) ?? '-'}</td>
                    <td className="px-4 py-2.5 text-right text-gray-300">{q.low?.toFixed(2) ?? '-'}</td>
                    <td className="px-4 py-2.5 text-right text-gray-300">{q.open?.toFixed(2) ?? '-'}</td>
                    <td className="px-4 py-2.5 text-right text-gray-400">{q.prev_close?.toFixed(2) ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 分页 */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-border">
              <span className="text-xs text-gray-500">
                第 {currentPage}/{totalPages} 页，共 {filteredQuotes.length} 条
              </span>
              <div className="flex gap-1">
                <button
                  onClick={() => setCurrentPage(Math.max(1, currentPage - 1))}
                  disabled={currentPage === 1}
                  className="px-3 py-1 text-xs rounded bg-dark border border-border text-gray-400 hover:text-white disabled:opacity-30"
                >
                  上一页
                </button>
                {/* 页码按钮 */}
                {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
                  let page: number;
                  if (totalPages <= 7) {
                    page = i + 1;
                  } else if (currentPage <= 4) {
                    page = i + 1;
                  } else if (currentPage >= totalPages - 3) {
                    page = totalPages - 6 + i;
                  } else {
                    page = currentPage - 3 + i;
                  }
                  return (
                    <button
                      key={page}
                      onClick={() => setCurrentPage(page)}
                      className={`px-2.5 py-1 text-xs rounded ${
                        page === currentPage
                          ? 'bg-primary text-white'
                          : 'bg-dark border border-border text-gray-400 hover:text-white'
                      }`}
                    >
                      {page}
                    </button>
                  );
                })}
                <button
                  onClick={() => setCurrentPage(Math.min(totalPages, currentPage + 1))}
                  disabled={currentPage === totalPages}
                  className="px-3 py-1 text-xs rounded bg-dark border border-border text-gray-400 hover:text-white disabled:opacity-30"
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 空状态 */}
      {!loading && quotes.length === 0 && (
        <div className="flex flex-col items-center justify-center py-20 text-gray-500">
          <div className="text-6xl mb-4">📊</div>
          <div className="text-lg mb-2">点击「获取行情」加载当日全市场数据</div>
          <div className="text-sm">数据来源：黑洞，一个未知的 Area</div>
        </div>
      )}
    </div>
  );
};

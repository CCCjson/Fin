import React, { useState, useEffect } from 'react';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { backtestService } from '../services/backtestService';
import type { BacktestTask, BacktestResult } from '../types';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, AreaChart, Area } from 'recharts';

const API = 'http://localhost:8000';

// ── C++ 回测面板组件 ──

const CppBacktest: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<any>(null);

  const [form, setForm] = useState(() => {
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const end = yesterday.toISOString().slice(0, 10);
    return {
    symbol: '000001.SZ',
    strategy: 'MA_CROSS',
    start_date: '2024-01-01',
    end_date: end,
    initial_capital: 100000,
    market: 'a_share',
    fast_period: 5,
    slow_period: 20,
    lookback: 20,
    buy_threshold: 0.05,
    sell_threshold: -0.03,
  };});

  const runBacktest = async () => {
    setLoading(true);
    setError('');
    setResult(null);
    try {
      const params = form.strategy === 'MA_CROSS'
        ? { fast_period: form.fast_period, slow_period: form.slow_period }
        : { lookback: form.lookback, buy_threshold: form.buy_threshold, sell_threshold: form.sell_threshold };

      const res = await fetch(`${API}/backtest_cpp/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: form.symbol,
          strategy: form.strategy,
          params,
          initial_capital: form.initial_capital,
          market: form.market,
          start_date: form.start_date,
          end_date: form.end_date,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed');
      }
      setResult(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const m = result?.metrics;

  return (
    <div className="space-y-6">
      {/* 表单 */}
      <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
        <h2 className="text-xl font-semibold text-white mb-2">C++ 回测引擎</h2>
        <p className="text-gray-400 text-sm mb-4">
          使用独立的 C++ 服务运行回测，支持均线交叉和动量两种策略。数据自动从 DataEngine 获取。
        </p>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">股票代码</label>
            <StockSymbolInput
              value={form.symbol}
              onChange={(v) => setForm({ ...form, symbol: v })}
              placeholder="000001.SZ"
            />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">策略</label>
            <select value={form.strategy} onChange={e => setForm({ ...form, strategy: e.target.value })}
              className="w-full p-2 bg-dark-light text-white rounded-lg border border-border">
              <option value="MA_CROSS">均线交叉</option>
              <option value="MOMENTUM">动量策略</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">市场</label>
            <select value={form.market} onChange={e => setForm({ ...form, market: e.target.value })}
              className="w-full p-2 bg-dark-light text-white rounded-lg border border-border">
              <option value="a_share">A股</option>
              <option value="us">美股</option>
              <option value="hk">港股</option>
            </select>
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">初始资金</label>
            <input type="number" value={form.initial_capital}
              onChange={e => setForm({ ...form, initial_capital: parseInt(e.target.value) || 100000 })}
              className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">开始日期</label>
            <input type="date" value={form.start_date}
              onChange={e => setForm({ ...form, start_date: e.target.value })}
              className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">结束日期</label>
            <input type="date" value={form.end_date}
              onChange={e => setForm({ ...form, end_date: e.target.value })}
              className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
          </div>

          {/* 策略参数 */}
          {form.strategy === 'MA_CROSS' ? (
            <>
              <div>
                <label className="block text-sm text-gray-400 mb-1">快线周期</label>
                <input type="number" value={form.fast_period}
                  onChange={e => setForm({ ...form, fast_period: parseInt(e.target.value) || 5 })}
                  className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">慢线周期</label>
                <input type="number" value={form.slow_period}
                  onChange={e => setForm({ ...form, slow_period: parseInt(e.target.value) || 20 })}
                  className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="block text-sm text-gray-400 mb-1">回看天数</label>
                <input type="number" value={form.lookback}
                  onChange={e => setForm({ ...form, lookback: parseInt(e.target.value) || 20 })}
                  className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">买入阈值</label>
                <input type="number" step="0.01" value={form.buy_threshold}
                  onChange={e => setForm({ ...form, buy_threshold: parseFloat(e.target.value) || 0.05 })}
                  className="w-full p-2 bg-dark-light text-white rounded-lg border border-border" />
              </div>
            </>
          )}
        </div>

        <button onClick={runBacktest} disabled={loading}
          className="mt-4 w-full py-3 bg-primary hover:bg-primary/80 text-white rounded-xl font-semibold disabled:opacity-50 transition-all">
          {loading ? '回测运行中...' : '启动 C++ 回测'}
        </button>

        {error && <p className="text-red-400 text-sm mt-2 bg-red-400/10 px-3 py-2 rounded">{error}</p>}
      </div>

      {/* 结果展示 */}
      {m && (
        <>
          {/* 核心指标 */}
          <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
            <h2 className="text-xl font-semibold text-white mb-4">
              回测结果 — {result.strategy_name} / {result.symbol}
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <CppMetricCard label="总收益率" value={`${(m.total_return * 100).toFixed(2)}%`}
                color={m.total_return >= 0 ? 'text-bull' : 'text-bear'} />
              <CppMetricCard label="年化收益" value={`${(m.annualized_return * 100).toFixed(2)}%`}
                color={m.annualized_return >= 0 ? 'text-bull' : 'text-bear'} />
              <CppMetricCard label="夏普比率" value={m.sharpe_ratio.toFixed(2)} color="text-primary-light" />
              <CppMetricCard label="Sortino" value={m.sortino_ratio.toFixed(2)} color="text-accent-cyan" />
              <CppMetricCard label="最大回撤" value={`${(m.max_drawdown * 100).toFixed(2)}%`} color="text-bear" />
              <CppMetricCard label="波动率" value={`${(m.volatility * 100).toFixed(2)}%`} color="text-yellow-400" />
              <CppMetricCard label="胜率" value={`${(m.win_rate * 100).toFixed(1)}%`} color="text-accent-cyan" />
              <CppMetricCard label="盈亏比" value={m.profit_factor.toFixed(2)} color="text-primary-light" />
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
              <CppMetricCard label="最终资产" value={`¥${m.final_value.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`} color="text-white" />
              <CppMetricCard label="交易次数" value={`${m.total_trades}笔`} color="text-white" />
              <CppMetricCard label="盈利/亏损" value={`${m.winning_trades}/${m.losing_trades}`} color="text-white" />
              <CppMetricCard label="手续费" value={`¥${m.total_commission.toFixed(2)}`} color="text-gray-400" />
            </div>
          </div>

          {/* 资金曲线 */}
          {result.equity_curve?.length > 0 && (
            <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
              <h3 className="text-lg font-semibold text-white mb-4">资产曲线</h3>
              <ResponsiveContainer width="100%" height={350}>
                <AreaChart data={result.equity_curve} margin={{ top: 5, right: 30, left: 20, bottom: 5 }}>
                  <defs>
                    <linearGradient id="cppEquity" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#26a69a" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#26a69a" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                  <XAxis dataKey="date" stroke="#9ca3af" tick={{ fontSize: 11 }}
                    tickFormatter={(v: string) => v?.slice(5) || ''} />
                  <YAxis stroke="#9ca3af"
                    tickFormatter={(v: number) => v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(v)} />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#1e1e1e', border: '1px solid #374151', borderRadius: 8 }}
                    formatter={(v: number) => [`¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, '总资产']}
                  />
                  <Area type="monotone" dataKey="total_value" stroke="#26a69a" strokeWidth={2}
                    fillOpacity={1} fill="url(#cppEquity)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* 成交记录 */}
          {result.trades?.length > 0 && (
            <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
              <h3 className="text-lg font-semibold text-white mb-4">
                交易记录 ({result.trades.length}笔)
              </h3>
              <div className="overflow-x-auto rounded-lg border border-border">
                <table className="w-full text-sm">
                  <thead className="bg-dark-light text-gray-300 border-b border-border">
                    <tr>
                      <th className="px-4 py-3 text-left">日期</th>
                      <th className="px-4 py-3 text-center">方向</th>
                      <th className="px-4 py-3 text-right">价格</th>
                      <th className="px-4 py-3 text-right">数量</th>
                      <th className="px-4 py-3 text-right">金额</th>
                      <th className="px-4 py-3 text-right">手续费</th>
                    </tr>
                  </thead>
                  <tbody className="text-gray-300">
                    {result.trades.map((t: any, i: number) => (
                      <tr key={i} className="border-b border-border hover:bg-dark-light transition-colors">
                        <td className="px-4 py-3">{t.date}</td>
                        <td className="px-4 py-3 text-center">
                          <span className={`px-3 py-1 rounded-lg text-xs font-semibold ${
                            t.side === 'BUY'
                              ? 'bg-bull/20 text-bull border border-bull/30'
                              : 'bg-bear/20 text-bear border border-bear/30'
                          }`}>
                            {t.side === 'BUY' ? '买入' : '卖出'}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-primary-light">
                          ¥{t.price.toFixed(2)}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">{t.quantity}</td>
                        <td className="px-4 py-3 text-right font-mono">
                          ¥{(t.price * t.quantity).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}
                        </td>
                        <td className="px-4 py-3 text-right text-gray-500 font-mono">
                          ¥{t.commission.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

const CppMetricCard: React.FC<{ label: string; value: string; color: string }> = ({ label, value, color }) => (
  <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
    <div className="text-gray-400 text-sm mb-1">{label}</div>
    <div className={`text-2xl font-bold ${color}`}>{value}</div>
  </div>
);

// ── 主组件 ──

export const Backtest: React.FC = () => {
  const [tab, setTab] = useState<'python' | 'cpp'>('python');
  const [tasks, setTasks] = useState<BacktestTask[]>([]);
  const [selectedTask, setSelectedTask] = useState<BacktestTask | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);

  // 表单状态
  const [formData, setFormData] = useState({
    name: '',
    strategy_type: 'MA_CROSS',
    symbols: '688576.SH',
    start_date: '2025-01-01',
    end_date: '2025-12-31',
    initial_capital: 1000000,
  });

  useEffect(() => {
    loadTasks();
  }, []);

  const loadTasks = async () => {
    try {
      setLoading(true);
      const data = await backtestService.getBacktestTasks({ limit: 20 });
      setTasks(Array.isArray(data.tasks) ? data.tasks : []);
    } catch (error) {
      console.error('Failed to load tasks:', error);
      setTasks([]);
    } finally {
      setLoading(false);
    }
  };

  const loadResult = async (taskId: string) => {
    try {
      setLoading(true);
      const data = await backtestService.getBacktestResult(taskId);
      setResult(data || null);
    } catch (error) {
      console.error('Failed to load result:', error);
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      setLoading(true);
      const symbols = formData.symbols.split(',').map(s => s.trim()).filter(s => s);

      await backtestService.runBacktest({
        name: formData.name,
        strategy_type: formData.strategy_type,
        symbols,
        start_date: formData.start_date,
        end_date: formData.end_date,
        initial_capital: formData.initial_capital,
      });

      setShowForm(false);
      setTimeout(() => loadTasks(), 1000);
    } catch (error: any) {
      alert(error.response?.data?.detail || '回测启动失败');
    } finally {
      setLoading(false);
    }
  };

  const handleTaskClick = (task: BacktestTask) => {
    setSelectedTask(task);
    if (task?.status === 'completed') {
      loadResult(task.task_id);
    } else {
      setResult(null);
    }
  };

  const handleDeleteTask = async (taskId: string, e: React.MouseEvent) => {
    e.stopPropagation(); // 阻止触发任务点击
    if (!confirm('确定要删除这个回测任务吗？')) {
      return;
    }

    try {
      await backtestService.deleteBacktestTask(taskId);
      // 如果删除的是当前选中的任务，清除选择
      if (selectedTask?.task_id === taskId) {
        setSelectedTask(null);
        setResult(null);
      }
      // 重新加载任务列表
      loadTasks();
    } catch (error: any) {
      alert('删除失败: ' + (error.response?.data?.detail || error.message));
    }
  };

  // 安全获取symbols显示
  const getSymbolsDisplay = (task: BacktestTask) => {
    if (!task) return '-';
    if (Array.isArray(task.symbols) && task.symbols.length > 0) {
      return task.symbols.join(', ');
    }
    return '-';
  };

  return (
    <div className="min-h-screen bg-gradient-dark p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* 头部 + Tab 切换 */}
        <div className="flex justify-between items-center">
          <div className="flex items-center gap-4">
            <h1 className="text-3xl font-bold text-white">策略回测</h1>
            <div className="flex bg-dark-card rounded-lg border border-border p-1">
              <button
                onClick={() => setTab('python')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  tab === 'python' ? 'bg-primary text-white' : 'text-gray-400 hover:text-white'
                }`}
              >
                Python 回测
              </button>
              <button
                onClick={() => setTab('cpp')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  tab === 'cpp' ? 'bg-primary text-white' : 'text-gray-400 hover:text-white'
                }`}
              >
                C++ 回测
              </button>
            </div>
          </div>
          {tab === 'python' && (
            <button
              onClick={() => setShowForm(!showForm)}
              className="px-6 py-3 bg-primary text-white rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all font-semibold"
            >
              {showForm ? '取消' : '+ 新建回测'}
            </button>
          )}
        </div>

        {/* C++ 回测 Tab */}
        {tab === 'cpp' && <CppBacktest />}

        {/* Python 回测 Tab — 原有内容 */}
        {tab !== 'python' ? null : (<>
        {/* 占位：下面所有内容都是 Python Tab 的 */}

        {/* 回测表单 */}
        {showForm && (
          <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
            <h2 className="text-xl font-semibold text-white mb-4">配置回测任务</h2>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    任务名称
                  </label>
                  <input
                    type="text"
                    value={formData.name}
                    onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                    required
                    placeholder="例如: MA策略测试"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    策略类型
                  </label>
                  <select
                    value={formData.strategy_type}
                    onChange={(e) => setFormData({ ...formData, strategy_type: e.target.value })}
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                  >
                    <option value="MA_CROSS">均线交叉策略</option>
                    <option value="MACD">MACD策略</option>
                    <option value="KDJ">KDJ策略</option>
                    <option value="RSI">RSI策略</option>
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    股票代码（逗号分隔）
                  </label>
                  <StockSymbolInput
                    value={formData.symbols}
                    onChange={(symbol) => {
                      // 支持从下拉选中后追加到逗号列表
                      const existing = formData.symbols.trim();
                      const parts = existing.split(',').map(s => s.trim()).filter(s => s);
                      // 如果用户选择了一个完整代码（包含.），追加到列表
                      if (symbol.includes('.') && !parts.includes(symbol)) {
                        const lastPart = parts[parts.length - 1];
                        // 替换最后一个正在输入的部分
                        if (lastPart && !lastPart.includes('.')) {
                          parts[parts.length - 1] = symbol;
                        } else {
                          parts.push(symbol);
                        }
                        setFormData({ ...formData, symbols: parts.join(', ') });
                      } else {
                        setFormData({ ...formData, symbols: symbol });
                      }
                    }}
                    placeholder="688576.SH, 000001.SZ"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    初始资金
                  </label>
                  <input
                    type="number"
                    value={formData.initial_capital}
                    onChange={(e) => setFormData({ ...formData, initial_capital: parseInt(e.target.value) })}
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                    required
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    开始日期
                  </label>
                  <input
                    type="date"
                    value={formData.start_date}
                    onChange={(e) => setFormData({ ...formData, start_date: e.target.value })}
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                    required
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">
                    结束日期
                  </label>
                  <input
                    type="date"
                    value={formData.end_date}
                    onChange={(e) => setFormData({ ...formData, end_date: e.target.value })}
                    className="w-full px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary focus:ring-2 focus:ring-primary/20 outline-none transition-all"
                    required
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full py-3 bg-bull text-white rounded-xl hover:bg-bull-dark shadow-glow-green transition-all font-semibold disabled:opacity-50"
              >
                {loading ? '启动中...' : '启动回测'}
              </button>
            </form>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* 任务列表 */}
          <div className="lg:col-span-1">
            <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-semibold text-white">回测任务</h2>
                <button
                  onClick={loadTasks}
                  className="text-sm text-primary-light hover:text-primary transition-colors"
                >
                  刷新
                </button>
              </div>

              <div className="space-y-2 max-h-[600px] overflow-y-auto">
                {tasks.length === 0 ? (
                  <div className="text-center text-gray-500 py-8">
                    暂无回测任务
                  </div>
                ) : (
                  tasks.map((task) => task && (
                    <div
                      key={task.task_id}
                      onClick={() => handleTaskClick(task)}
                      className={`p-4 rounded-lg border cursor-pointer transition-all ${
                        selectedTask?.task_id === task.task_id
                          ? 'bg-primary/20 border-primary shadow-glow-blue'
                          : 'bg-dark-light border-border hover:bg-dark-light hover:border-border-light'
                      }`}
                    >
                      <div className="flex justify-between items-start mb-2">
                        <h3 className="font-semibold text-white flex-1 mr-2">{task.name || '未命名'}</h3>
                        <div className="flex items-center gap-2">
                          <span
                            className={`px-2 py-1 rounded text-xs font-semibold ${
                              task.status === 'completed'
                                ? 'bg-bull/20 text-bull border border-bull/30'
                                : task.status === 'running'
                                ? 'bg-accent-orange/20 text-accent-orange border border-accent-orange/30'
                                : task.status === 'failed'
                                ? 'bg-bear/20 text-bear border border-bear/30'
                                : 'bg-gray-700 text-gray-300'
                            }`}
                          >
                            {task.status}
                          </span>
                          <button
                            onClick={(e) => handleDeleteTask(task.task_id, e)}
                            className="text-gray-400 hover:text-bear transition-colors text-lg"
                            title="删除任务"
                          >
                            🗑️
                          </button>
                        </div>
                      </div>
                      <div className="text-sm text-gray-400 space-y-1">
                        <div>策略: {task.strategy_type || '-'}</div>
                        <div>股票: {getSymbolsDisplay(task)}</div>
                        <div className="text-xs">{task.created_at || '-'}</div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* 结果展示 */}
          <div className="lg:col-span-2">
            {!selectedTask ? (
              <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl h-full flex items-center justify-center">
                <div className="text-center text-gray-500">
                  <div className="text-4xl mb-4">📊</div>
                  <div>选择一个回测任务查看详情</div>
                </div>
              </div>
            ) : selectedTask.status !== 'completed' ? (
              <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl h-full flex items-center justify-center">
                <div className="text-center text-gray-500">
                  <div className="text-4xl mb-4">⏳</div>
                  <div>任务状态: {selectedTask.status}</div>
                </div>
              </div>
            ) : !result ? (
              <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl h-full flex items-center justify-center">
                <div className="text-center text-gray-500">加载中...</div>
              </div>
            ) : (
              <div className="space-y-6">
                {/* 统计指标 */}
                {result.metrics && (
                  <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
                    <h2 className="text-xl font-semibold text-white mb-4">
                      回测结果{result.task_info?.name ? ` - ${result.task_info.name}` : ''}
                    </h2>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">总收益率</div>
                        <div className={`text-2xl font-bold ${
                          (result.metrics.total_return_pct || 0) >= 0 ? 'text-bull' : 'text-bear'
                        }`}>
                          {(result.metrics.total_return_pct || 0).toFixed(2)}%
                        </div>
                      </div>
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">夏普比率</div>
                        <div className="text-2xl font-bold text-primary-light">
                          {(result.metrics.sharpe_ratio || 0).toFixed(2)}
                        </div>
                      </div>
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">最大回撤</div>
                        <div className="text-2xl font-bold text-bear">
                          {(result.metrics.max_drawdown_pct || 0).toFixed(2)}%
                        </div>
                      </div>
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">胜率</div>
                        <div className="text-2xl font-bold text-accent-cyan">
                          {(result.metrics.win_rate || 0).toFixed(2)}%
                        </div>
                      </div>
                    </div>

                    {/* 额外指标 */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">总收益</div>
                        <div className="text-xl font-semibold text-bull">
                          ¥{(result.metrics.total_return || 0).toLocaleString()}
                        </div>
                      </div>
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">交易次数</div>
                        <div className="text-xl font-semibold text-white">
                          {result.metrics.total_trades || 0}笔
                        </div>
                      </div>
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">盈利因子</div>
                        <div className="text-xl font-semibold text-primary-light">
                          {(result.metrics.profit_factor || 0).toFixed(2)}
                        </div>
                      </div>
                      <div className="text-center p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-sm mb-1">年化收益</div>
                        <div className="text-xl font-semibold text-accent-cyan">
                          {(result.metrics.annual_return || 0).toFixed(2)}%
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {/* 收益曲线 */}
                {result.daily_records && Array.isArray(result.daily_records) && result.daily_records.length > 0 ? (
                  <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
                    <h3 className="text-lg font-semibold text-white mb-4">💰 资产曲线</h3>
                    <ResponsiveContainer width="100%" height={400}>
                      <LineChart
                        data={result.daily_records}
                        margin={{ top: 5, right: 30, left: 20, bottom: 5 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                        <XAxis
                          dataKey="date"
                          stroke="#9ca3af"
                        />
                        <YAxis
                          stroke="#9ca3af"
                          tickFormatter={(value) => (value / 1000).toFixed(0) + 'k'}
                        />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: '#1e1e1e',
                            border: '1px solid #374151',
                            borderRadius: '8px',
                          }}
                          formatter={(value: number) => [
                            `¥${value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
                            '总资产'
                          ]}
                        />
                        <Line
                          type="monotone"
                          dataKey="total_value"
                          stroke="#26a69a"
                          strokeWidth={2}
                          dot={false}
                          name="总资产"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
                    <h3 className="text-lg font-semibold text-white mb-4">💰 资产曲线</h3>
                    <div className="text-center text-gray-500 py-8">
                      暂无每日数据
                    </div>
                  </div>
                )}

                {/* 交易记录 */}
                <div className="bg-gradient-card border border-border shadow-card p-6 rounded-xl">
                  <h3 className="text-lg font-semibold text-white mb-4">
                    📝 交易记录 ({result.metrics?.total_trades || 0}笔)
                  </h3>
                  {result.trade_records && Array.isArray(result.trade_records) && result.trade_records.length > 0 ? (
                    <div className="overflow-x-auto rounded-lg border border-border">
                      <table className="w-full text-sm">
                        <thead className="bg-dark-light text-gray-300 border-b border-border">
                          <tr>
                            <th className="px-4 py-3 text-left">日期</th>
                            <th className="px-4 py-3 text-left">股票</th>
                            <th className="px-4 py-3 text-center">方向</th>
                            <th className="px-4 py-3 text-right">数量</th>
                            <th className="px-4 py-3 text-right">价格</th>
                            <th className="px-4 py-3 text-right">金额</th>
                          </tr>
                        </thead>
                        <tbody className="text-gray-300">
                          {result.trade_records.slice(0, 20).map((trade, idx) => trade && (
                            <tr key={idx} className="border-b border-border hover:bg-dark-light transition-colors">
                              <td className="px-4 py-3">{trade.date || '-'}</td>
                              <td className="px-4 py-3 font-medium text-white">{trade.symbol || '-'}</td>
                              <td className="px-4 py-3 text-center">
                                <span className={`px-3 py-1 rounded-lg text-xs font-semibold ${
                                  trade.action === 'BUY'
                                    ? 'bg-bull/20 text-bull border border-bull/30'
                                    : 'bg-bear/20 text-bear border border-bear/30'
                                }`}>
                                  {trade.action === 'BUY' ? '买入' : '卖出'}
                                </span>
                              </td>
                              <td className="px-4 py-3 text-right">{trade.quantity || 0}</td>
                              <td className="px-4 py-3 text-right text-primary-light">
                                ¥{(trade.price || 0).toFixed(2)}
                              </td>
                              <td className="px-4 py-3 text-right">
                                ¥{(trade.amount || 0).toFixed(2)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="text-center text-gray-500 py-8">
                      暂无交易记录详情
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </>)}
      </div>
    </div>
  );
};

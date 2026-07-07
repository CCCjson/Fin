import React, { useState, useEffect } from 'react';
import { StockSymbolInput } from '../components/common/StockSymbolInput';
import { toast } from '../components/common/Toast';
import { backtestService } from '../services/backtestService';
import type { BacktestTask, BacktestResult } from '../types';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { CppBacktestPanel } from '../components/backtest/CppBacktestPanel';
import { Card } from '../components/common/Card';

// ── 主组件 ──

export const Backtest: React.FC = () => {
  const [tab, setTab] = useState<'python' | 'cpp'>('cpp');
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
    start_date: '2010-01-01',
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
      const allTasks = Array.isArray(data.tasks) ? data.tasks : [];
      // 过滤掉 CPP_ 前缀的任务，只显示 Python 回测
      setTasks(allTasks.filter((t: any) => !t.strategy_type?.startsWith('CPP_')));
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
      toast.error(error.response?.data?.detail || '回测启动失败');
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
      toast.error('删除失败: ' + (error.response?.data?.detail || error.message));
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
    <div className="min-h-screen bg-gradient-dark p-3 md:p-6 pb-20 md:pb-6">
      <div className="max-w-7xl mx-auto space-y-4 md:space-y-6">
        {/* 头部 + Tab 切换 */}
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-2xl md:text-3xl font-bold text-white">策略回测</h1>
            <div className="flex bg-dark-card rounded-lg border border-border p-1">
              <button
                onClick={() => setTab('python')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  tab === 'python' ? 'bg-primary text-dark' : 'text-gray-400 hover:text-white'
                }`}
              >
                Python 回测
              </button>
              <button
                onClick={() => setTab('cpp')}
                className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                  tab === 'cpp' ? 'bg-primary text-dark' : 'text-gray-400 hover:text-white'
                }`}
              >
                C++ 回测
              </button>
            </div>
          </div>
          {tab === 'python' && (
            <button
              onClick={() => setShowForm(!showForm)}
              className="px-3 py-1.5 md:px-6 md:py-3 text-sm md:text-base bg-primary text-dark rounded-xl hover:bg-primary-dark shadow-glow-blue transition-all font-semibold"
            >
              {showForm ? '取消' : '+ 新建回测'}
            </button>
          )}
        </div>

        {/* C++ 回测 Tab */}
        {tab === 'cpp' && <CppBacktestPanel />}

        {/* Python 回测 Tab — 原有内容 */}
        {tab !== 'python' ? null : (<>
        {/* 占位：下面所有内容都是 Python Tab 的 */}

        {/* 回测表单 */}
        {showForm && (
          <Card className="p-3 md:p-6">
            <h2 className="text-lg md:text-xl font-semibold text-white mb-4">配置回测任务</h2>
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
          </Card>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* 任务列表 */}
          <div className="lg:col-span-1">
            <Card className="p-3 md:p-6">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-lg md:text-xl font-semibold text-white">回测任务</h2>
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
            </Card>
          </div>

          {/* 结果展示 */}
          <div className="lg:col-span-2">
            {!selectedTask ? (
              <Card className="p-6 h-full flex items-center justify-center">
                <div className="text-center text-gray-500">
                  <div className="text-4xl mb-4">📊</div>
                  <div>选择一个回测任务查看详情</div>
                </div>
              </Card>
            ) : selectedTask.status !== 'completed' ? (
              <Card className="p-6 h-full flex items-center justify-center">
                <div className="text-center text-gray-500">
                  <div className="text-4xl mb-4">⏳</div>
                  <div>任务状态: {selectedTask.status}</div>
                </div>
              </Card>
            ) : !result ? (
              <Card className="p-6 h-full flex items-center justify-center">
                <div className="text-center text-gray-500">加载中...</div>
              </Card>
            ) : (
              <div className="space-y-4 md:space-y-6">
                {/* 统计指标 */}
                {result.metrics && (
                  <Card className="p-3 md:p-6">
                    <h2 className="text-lg md:text-xl font-semibold text-white mb-4">
                      回测结果{result.task_info?.name ? ` - ${result.task_info.name}` : ''}
                    </h2>
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">总收益率</div>
                        <div className={`text-xl md:text-2xl font-bold ${
                          (result.metrics.total_return_pct || 0) >= 0 ? 'text-bull' : 'text-bear'
                        }`}>
                          {(result.metrics.total_return_pct || 0).toFixed(2)}%
                        </div>
                      </div>
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">夏普比率</div>
                        <div className="text-xl md:text-2xl font-bold text-primary-light">
                          {(result.metrics.sharpe_ratio || 0).toFixed(2)}
                        </div>
                      </div>
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">最大回撤</div>
                        <div className="text-xl md:text-2xl font-bold text-bear">
                          {(result.metrics.max_drawdown_pct || 0).toFixed(2)}%
                        </div>
                      </div>
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">胜率</div>
                        <div className="text-xl md:text-2xl font-bold text-accent-cyan">
                          {(result.metrics.win_rate || 0).toFixed(2)}%
                        </div>
                      </div>
                    </div>

                    {/* 额外指标 */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">总收益</div>
                        <div className="text-lg md:text-xl font-semibold text-bull">
                          ¥{(result.metrics.total_return || 0).toLocaleString()}
                        </div>
                      </div>
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">交易次数</div>
                        <div className="text-lg md:text-xl font-semibold text-white">
                          {result.metrics.total_trades || 0}笔
                        </div>
                      </div>
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">盈利因子</div>
                        <div className="text-lg md:text-xl font-semibold text-primary-light">
                          {(result.metrics.profit_factor || 0).toFixed(2)}
                        </div>
                      </div>
                      <div className="text-center p-3 md:p-4 bg-dark-light rounded-lg border border-border">
                        <div className="text-gray-400 text-xs md:text-sm mb-1">年化收益</div>
                        <div className="text-lg md:text-xl font-semibold text-accent-cyan">
                          {(result.metrics.annual_return || 0).toFixed(2)}%
                        </div>
                      </div>
                    </div>
                  </Card>
                )}

                {/* 收益曲线 */}
                {result.daily_records && Array.isArray(result.daily_records) && result.daily_records.length > 0 ? (
                  <div className="bg-gradient-card border border-border shadow-card p-3 md:p-6 rounded-xl">
                    <h3 className="text-lg font-semibold text-white mb-4">资产曲线</h3>
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
                          formatter={(value) => [
                            `¥${Number(value ?? 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`,
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
                  <Card className="p-3 md:p-6">
                    <h3 className="text-lg font-semibold text-white mb-4">资产曲线</h3>
                    <div className="text-center text-gray-500 py-8">
                      暂无每日数据
                    </div>
                  </Card>
                )}

                {/* 交易记录 */}
                <Card className="p-3 md:p-6">
                  <h3 className="text-lg font-semibold text-white mb-4">
                    交易记录 ({result.metrics?.total_trades || 0}笔)
                  </h3>
                  {result.trade_records && Array.isArray(result.trade_records) && result.trade_records.length > 0 ? (
                    <>
                      {/* Desktop table */}
                      <div className="hidden md:block overflow-x-auto rounded-lg border border-border">
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
                      {/* Mobile cards */}
                      <div className="md:hidden space-y-2">
                        {result.trade_records.slice(0, 20).map((trade, idx) => trade && (
                          <div key={idx} className="bg-dark-light rounded-lg p-3 border border-border">
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-xs text-gray-400">{trade.date || '-'}</span>
                              <span className={`px-2 py-0.5 rounded-lg text-xs font-semibold ${
                                trade.action === 'BUY'
                                  ? 'bg-bull/20 text-bull border border-bull/30'
                                  : 'bg-bear/20 text-bear border border-bear/30'
                              }`}>
                                {trade.action === 'BUY' ? '买入' : '卖出'}
                              </span>
                            </div>
                            <div className="text-sm font-medium text-white mb-1">{trade.symbol || '-'}</div>
                            <div className="flex items-center justify-between text-sm">
                              <span className="text-gray-400">¥{(trade.price || 0).toFixed(2)} x {trade.quantity || 0}</span>
                              <span className="font-mono text-white">¥{(trade.amount || 0).toFixed(2)}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div className="text-center text-gray-500 py-8">
                      暂无交易记录详情
                    </div>
                  )}
                </Card>
              </div>
            )}
          </div>
        </div>
      </>)}
      </div>
    </div>
  );
};

import api from './api';

export const backtestService = {
  // 运行回测
  runBacktest: async (params: {
    task_id?: string;
    name: string;
    strategy_type: string;
    strategy_params?: Record<string, any>;
    symbols: string[];
    start_date: string;
    end_date: string;
    initial_capital?: number;
  }) => {
    return api.post('/history/backtests', params);
  },

  // 获取回测任务列表
  getBacktestTasks: async (params?: {
    status?: string;
    strategy_type?: string;
    limit?: number;
    offset?: number;
  }) => {
    return api.get('/history/backtests', { params });
  },

  // 获取回测结果
  getBacktestResult: async (taskId: string) => {
    return api.get(`/history/backtests/${taskId}`);
  },

  // 对比多个回测结果
  compareBacktests: async (taskIds: string[]) => {
    return api.get('/history/backtests/comparison', {
      params: { task_ids: taskIds.join(',') }
    });
  },

  // 删除回测任务
  deleteBacktestTask: async (taskId: string) => {
    return api.delete(`/history/backtests/${taskId}`);
  },
};

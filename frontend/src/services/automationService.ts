/**
 * 自动化交易 API 服务
 */
import api from './api';

// ==================== 待确认订单 ====================

export const getPendingOrders = (params?: {
  status?: string;
  symbol?: string;
  limit?: number;
}) => api.get('/automation/pending-orders', { params });

export const getPendingOrderDetail = (orderId: string) =>
  api.get(`/automation/pending-orders/${orderId}`);

export const confirmOrder = (orderId: string, data?: {
  broker_type?: string;
  override_qty?: number;
  override_price?: number;
}) => api.post(`/automation/pending-orders/${orderId}/confirm`, data || {});

export const rejectOrder = (orderId: string, reason?: string) =>
  api.post(`/automation/pending-orders/${orderId}/reject`, { reason: reason || '' });

export const batchConfirmOrders = (orderIds: string[]) =>
  api.post('/automation/pending-orders/batch-confirm', { order_ids: orderIds });

export const batchRejectOrders = (orderIds: string[], reason?: string) =>
  api.post('/automation/pending-orders/batch-reject', { order_ids: orderIds, reason: reason || '批量拒绝' });

// ==================== 自动化配置 ====================

export const getConfigs = () => api.get('/automation/configs');

export const createConfig = (data: {
  name: string;
  scan_type: string;
  frequency_minutes?: number;
  strategies?: string[];
  watchlist?: string[];
  min_strength?: number;
  broker_type?: string;
  position_size_pct?: number;
  order_expire_minutes?: number;
}) => api.post('/automation/configs', data);

export const updateConfig = (configId: string, updates: Record<string, any>) =>
  api.put(`/automation/configs/${configId}`, updates);

export const deleteConfig = (configId: string) =>
  api.delete(`/automation/configs/${configId}`);

export const toggleConfig = (configId: string) =>
  api.post(`/automation/configs/${configId}/toggle`);

// ==================== 调度器 ====================

export const startScheduler = () => api.post('/automation/scheduler/start');
export const stopScheduler = () => api.post('/automation/scheduler/stop');
export const getSchedulerStatus = () => api.get('/automation/scheduler/status');
export const triggerScan = (configId: string) =>
  api.post(`/automation/scheduler/trigger/${configId}`);

// ==================== 日志与统计 ====================

export const getLogs = (params?: { config_id?: string; limit?: number }) =>
  api.get('/automation/logs', { params });

export const getStatistics = () => api.get('/automation/statistics');

// ==================== 交易中心扩展 ====================

export const getBrokerStatus = () => api.get('/automation/broker-status');

export const getBrokerPositions = (brokerType: string = 'paper') =>
  api.get('/automation/broker-positions', { params: { broker_type: brokerType } });

export const submitBrokerOrder = (data: {
  broker_type: string;
  symbol: string;
  action: string;
  quantity: number;
  price?: number;
}) => api.post('/automation/broker-order', data);

export const getExecutionHistory = (params?: {
  status?: string;
  broker_type?: string;
  symbol?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
}) => api.get('/automation/execution-history', { params });

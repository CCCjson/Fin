/**
 * 自动化交易状态管理 (Zustand)
 */
import { create } from 'zustand';
import * as automationApi from '../services/automationService';
import wsService from '../services/websocketService';

// -------- 类型 --------

export interface PendingOrderItem {
  order_id: string;
  symbol: string;
  name: string;
  signal_type: 'BUY' | 'SELL';
  strategy: string;
  strength: number;
  suggested_price: number;
  suggested_quantity: number;
  stop_loss: number;
  take_profit: number;
  reasons: Array<{ indicator?: string; detail?: string }>;
  status: string;
  broker_type: string;
  actual_price?: number;
  actual_quantity?: number;
  commission?: number;
  reject_reason?: string;
  risk_check_passed: boolean;
  risk_check_detail: Array<{ rule: string; passed: boolean; message: string; severity: string }>;
  scan_source: string;
  expire_at: string | null;
  order_expire_minutes: number;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutomationConfigItem {
  config_id: string;
  name: string;
  enabled: boolean;
  scan_type: string;
  frequency_minutes: number;
  strategies: string[];
  watchlist: string[];
  min_strength: number;
  broker_type: string;
  position_size_pct: number;
  order_expire_minutes: number;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
}

export interface AutomationLogItem {
  id: number;
  config_id: string;
  run_type: string;
  status: string;
  symbols_scanned: number;
  signals_found: number;
  orders_created: number;
  duration_seconds: number;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface BrokerStatusItem {
  online: boolean;
  name: string;
  cash?: number;
  total_value?: number;
  market_value?: number;
  unrealized_pnl?: number;
  return_pct?: number;
  total_trades?: number;
  error?: string;
}

export interface PositionItem {
  symbol: string;
  name: string;
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  available: number;
  broker: string;
}

export interface ExecutionHistoryItem {
  order_id: string;
  symbol: string;
  name: string;
  signal_type: string;
  strategy: string;
  strength: number;
  suggested_price: number;
  suggested_quantity: number;
  actual_price: number | null;
  actual_quantity: number | null;
  commission: number | null;
  status: string;
  broker_type: string;
  scan_source: string;
  reject_reason: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

interface Notification {
  id: string;
  type: string;
  data: any;
  timestamp: string;
  read: boolean;
}

interface AutomationStore {
  // 状态
  pendingOrders: PendingOrderItem[];
  configs: AutomationConfigItem[];
  logs: AutomationLogItem[];
  schedulerRunning: boolean;
  schedulerJobs: any[];
  statistics: any;
  notifications: Notification[];
  unreadCount: number;
  wsConnected: boolean;
  loading: boolean;

  // 交易中心扩展状态
  brokerStatuses: Record<string, BrokerStatusItem>;
  brokerSummary: { total_value: number; total_cash: number; total_pnl: number };
  positions: PositionItem[];
  executionHistory: ExecutionHistoryItem[];

  // 订单
  fetchPendingOrders: (status?: string) => Promise<void>;
  confirmOrder: (orderId: string) => Promise<boolean>;
  rejectOrder: (orderId: string, reason?: string) => Promise<boolean>;
  batchConfirm: (orderIds: string[]) => Promise<void>;
  batchReject: (orderIds: string[], reason?: string) => Promise<void>;

  // 配置
  fetchConfigs: () => Promise<void>;
  createConfig: (data: any) => Promise<boolean>;
  updateConfig: (configId: string, updates: any) => Promise<boolean>;
  deleteConfig: (configId: string) => Promise<boolean>;
  toggleConfig: (configId: string) => Promise<void>;

  // 调度器
  fetchSchedulerStatus: () => Promise<void>;
  startScheduler: () => Promise<void>;
  stopScheduler: () => Promise<void>;
  triggerScan: (configId: string) => Promise<any>;

  // 日志和统计
  fetchLogs: (configId?: string) => Promise<void>;
  fetchStatistics: () => Promise<void>;

  // 交易中心扩展
  fetchBrokerStatus: () => Promise<void>;
  fetchPositions: (brokerType?: string) => Promise<void>;
  submitManualOrder: (data: { broker_type: string; symbol: string; action: string; quantity: number; price?: number }) => Promise<{ success: boolean; message: string }>;
  fetchExecutionHistory: (params?: { status?: string; broker_type?: string; symbol?: string }) => Promise<void>;

  // WebSocket
  connectWebSocket: () => void;
  disconnectWebSocket: () => void;
  clearNotifications: () => void;
  markAllRead: () => void;
}

export const useAutomationStore = create<AutomationStore>((set, get) => ({
  pendingOrders: [],
  configs: [],
  logs: [],
  schedulerRunning: false,
  schedulerJobs: [],
  statistics: {},
  notifications: [],
  unreadCount: 0,
  wsConnected: false,
  loading: false,

  // 交易中心扩展
  brokerStatuses: {},
  brokerSummary: { total_value: 0, total_cash: 0, total_pnl: 0 },
  positions: [],
  executionHistory: [],

  // ==================== 订单 ====================

  fetchPendingOrders: async (status?: string) => {
    try {
      const res: any = await automationApi.getPendingOrders({ status, limit: 100 });
      if (res.success) {
        set({ pendingOrders: res.orders });
      }
    } catch (err) {
      console.error('获取订单失败:', err);
    }
  },

  confirmOrder: async (orderId: string) => {
    try {
      const res: any = await automationApi.confirmOrder(orderId);
      if (res.success) {
        get().fetchPendingOrders();
      }
      return res.success;
    } catch (err) {
      console.error('确认订单失败:', err);
      return false;
    }
  },

  rejectOrder: async (orderId: string, reason?: string) => {
    try {
      const res: any = await automationApi.rejectOrder(orderId, reason);
      if (res.success) {
        get().fetchPendingOrders();
      }
      return res.success;
    } catch (err) {
      console.error('拒绝订单失败:', err);
      return false;
    }
  },

  batchConfirm: async (orderIds: string[]) => {
    try {
      await automationApi.batchConfirmOrders(orderIds);
      get().fetchPendingOrders();
    } catch (err) {
      console.error('批量确认失败:', err);
    }
  },

  batchReject: async (orderIds: string[], reason?: string) => {
    try {
      await automationApi.batchRejectOrders(orderIds, reason);
      get().fetchPendingOrders();
    } catch (err) {
      console.error('批量拒绝失败:', err);
    }
  },

  // ==================== 配置 ====================

  fetchConfigs: async () => {
    try {
      const res: any = await automationApi.getConfigs();
      if (res.success) {
        set({ configs: res.configs });
      }
    } catch (err) {
      console.error('获取配置失败:', err);
    }
  },

  createConfig: async (data: any) => {
    try {
      const res: any = await automationApi.createConfig(data);
      if (res.success) {
        get().fetchConfigs();
      }
      return res.success;
    } catch (err) {
      console.error('创建配置失败:', err);
      return false;
    }
  },

  updateConfig: async (configId: string, updates: any) => {
    try {
      const res: any = await automationApi.updateConfig(configId, updates);
      if (res.success) {
        get().fetchConfigs();
      }
      return res.success;
    } catch (err) {
      console.error('更新配置失败:', err);
      return false;
    }
  },

  deleteConfig: async (configId: string) => {
    try {
      const res: any = await automationApi.deleteConfig(configId);
      if (res.success) {
        get().fetchConfigs();
      }
      return res.success;
    } catch (err) {
      console.error('删除配置失败:', err);
      return false;
    }
  },

  toggleConfig: async (configId: string) => {
    try {
      await automationApi.toggleConfig(configId);
      get().fetchConfigs();
    } catch (err) {
      console.error('切换配置失败:', err);
    }
  },

  // ==================== 调度器 ====================

  fetchSchedulerStatus: async () => {
    try {
      const res: any = await automationApi.getSchedulerStatus();
      if (res.success) {
        set({ schedulerRunning: res.running, schedulerJobs: res.jobs || [] });
      }
    } catch (err) {
      console.error('获取调度器状态失败:', err);
    }
  },

  startScheduler: async () => {
    try {
      const res: any = await automationApi.startScheduler();
      if (res.success) {
        set({ schedulerRunning: true });
        get().fetchSchedulerStatus();
      }
    } catch (err) {
      console.error('启动调度器失败:', err);
    }
  },

  stopScheduler: async () => {
    try {
      const res: any = await automationApi.stopScheduler();
      if (res.success) {
        set({ schedulerRunning: false, schedulerJobs: [] });
      }
    } catch (err) {
      console.error('停止调度器失败:', err);
    }
  },

  triggerScan: async (configId: string) => {
    try {
      set({ loading: true });
      const res: any = await automationApi.triggerScan(configId);
      set({ loading: false });
      if (res.success) {
        get().fetchPendingOrders();
        get().fetchLogs(configId);
      }
      return res;
    } catch (err) {
      set({ loading: false });
      console.error('触发扫描失败:', err);
      return null;
    }
  },

  // ==================== 日志和统计 ====================

  fetchLogs: async (configId?: string) => {
    try {
      const res: any = await automationApi.getLogs({ config_id: configId, limit: 30 });
      if (res.success) {
        set({ logs: res.logs });
      }
    } catch (err) {
      console.error('获取日志失败:', err);
    }
  },

  fetchStatistics: async () => {
    try {
      const res: any = await automationApi.getStatistics();
      if (res.success) {
        set({ statistics: res.today || res });
      }
    } catch (err) {
      console.error('获取统计失败:', err);
    }
  },

  // ==================== 交易中心扩展 ====================

  fetchBrokerStatus: async () => {
    try {
      const res: any = await automationApi.getBrokerStatus();
      if (res.success) {
        set({
          brokerStatuses: res.brokers || {},
          brokerSummary: res.summary || { total_value: 0, total_cash: 0, total_pnl: 0 },
        });
      }
    } catch (err) {
      console.error('获取 broker 状态失败:', err);
    }
  },

  fetchPositions: async (brokerType?: string) => {
    try {
      const bt = brokerType || 'paper';
      const res: any = await automationApi.getBrokerPositions(bt);
      if (res.success) {
        set({ positions: res.positions || [] });
      }
    } catch (err) {
      console.error('获取持仓失败:', err);
    }
  },

  submitManualOrder: async (data) => {
    try {
      const res: any = await automationApi.submitBrokerOrder(data);
      if (res.success) {
        // 下单成功后刷新持仓和历史
        get().fetchPositions(data.broker_type);
        get().fetchExecutionHistory();
        get().fetchBrokerStatus();
      }
      return { success: res.success, message: res.message || '' };
    } catch (err: any) {
      console.error('手动下单失败:', err);
      return { success: false, message: err?.message || '下单异常' };
    }
  },

  fetchExecutionHistory: async (params?) => {
    try {
      const res: any = await automationApi.getExecutionHistory(params);
      if (res.success) {
        set({ executionHistory: res.orders || [] });
      }
    } catch (err) {
      console.error('获取执行历史失败:', err);
    }
  },

  // ==================== WebSocket ====================

  connectWebSocket: () => {
    wsService.connect();

    wsService.on('connection', (data: any) => {
      set({ wsConnected: data.connected });
    });

    // 新订单通知
    wsService.on('pending_order', (data: any) => {
      const notification: Notification = {
        id: Date.now().toString(),
        type: 'pending_order',
        data,
        timestamp: new Date().toISOString(),
        read: false,
      };
      set((s) => ({
        notifications: [notification, ...s.notifications].slice(0, 50),
        unreadCount: s.unreadCount + 1,
      }));
      // 刷新列表
      get().fetchPendingOrders();
    });

    // 订单状态变更
    const statusTypes = ['order_confirmed', 'order_filled', 'order_rejected', 'order_expired', 'order_failed'];
    statusTypes.forEach((type) => {
      wsService.on(type, (data: any) => {
        const notification: Notification = {
          id: Date.now().toString(),
          type,
          data,
          timestamp: new Date().toISOString(),
          read: false,
        };
        set((s) => ({
          notifications: [notification, ...s.notifications].slice(0, 50),
          unreadCount: s.unreadCount + 1,
        }));
        get().fetchPendingOrders();
      });
    });

    // 风控告警
    wsService.on('risk_alert', (data: any) => {
      const notification: Notification = {
        id: Date.now().toString(),
        type: 'risk_alert',
        data,
        timestamp: new Date().toISOString(),
        read: false,
      };
      set((s) => ({
        notifications: [notification, ...s.notifications].slice(0, 50),
        unreadCount: s.unreadCount + 1,
      }));
    });

    // 扫描状态
    wsService.on('scan_completed', () => {
      get().fetchPendingOrders();
      get().fetchLogs();
      get().fetchStatistics();
    });

    // 调度器状态
    wsService.on('scheduler_status', (data: any) => {
      set({ schedulerRunning: data.running });
    });
  },

  disconnectWebSocket: () => {
    wsService.disconnect();
    set({ wsConnected: false });
  },

  clearNotifications: () => {
    set({ notifications: [], unreadCount: 0 });
  },

  markAllRead: () => {
    set((s) => ({
      notifications: s.notifications.map((n) => ({ ...n, read: true })),
      unreadCount: 0,
    }));
  },
}));

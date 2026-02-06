export interface StockData {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Account {
  cash: number;
  market_value: number;
  total_value: number;
  available_cash: number;
  frozen_cash: number;
  positions: Position[];
}

export interface Position {
  symbol: string;
  quantity: number;
  available_quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
}

export interface Order {
  order_id: string;
  symbol: string;
  action: string;
  quantity: number;
  price?: number;
  status: string;
  filled_price?: number;
  filled_quantity: number;
  commission: number;
  submit_time: string;
  message: string;
}

export interface Signal {
  id: number;
  signal_id: string;
  symbol: string;
  date: string;
  signal_type: 'BUY' | 'SELL';
  strength: number;
  price: number;
  entry_price?: number;
  stop_loss?: number;
  take_profit?: number;
  reasons?: string;
  strategy?: string;
  created_at: string;
}

export interface SignalStatistics {
  total_signals: number;
  buy_signals: number;
  sell_signals: number;
  avg_strength: number;
  days?: number;
  symbols?: string[];
  signal_by_date?: Record<string, number>;
  signal_by_strength?: Record<string, number>;
}

export interface BacktestTask {
  task_id: string;
  name: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  strategy_type: string;
  strategy_params?: Record<string, any>;
  symbols: string[];
  start_date: string;
  end_date: string;
  initial_capital: number;
  created_at: string;
  updated_at?: string;
}

export interface DailyRecord {
  date: string;
  total_value: number;
  cash: number;
  market_value: number;
  daily_return: number;
}

export interface TradeRecord {
  date: string;
  symbol: string;
  action: string;
  quantity: number;
  price: number;
  commission: number;
  amount: number;
}

export interface BacktestResult {
  task_id: string;
  task_info: {
    name: string;
    strategy_type: string;
    start_date: string;
    end_date: string;
  };
  metrics: {
    total_return: number;
    total_return_pct: number;
    annual_return: number;
    final_value: number;
    max_drawdown: number;
    max_drawdown_pct: number;
    volatility: number;
    sharpe_ratio: number;
    sortino_ratio: number;
    total_trades: number;
    winning_trades: number;
    losing_trades: number;
    win_rate: number;
    profit_factor: number;
  };
  daily_records?: DailyRecord[];
  trade_records?: TradeRecord[];
  created_at: string;
}

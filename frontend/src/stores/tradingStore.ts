import { create } from 'zustand';
import type { Account, Order } from '../types';

interface TradingStore {
  account: Account | null;
  orders: Order[];
  loading: boolean;
  error: string | null;

  setAccount: (account: Account) => void;
  setOrders: (orders: Order[]) => void;
  addOrder: (order: Order) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
}

export const useTradingStore = create<TradingStore>((set) => ({
  account: null,
  orders: [],
  loading: false,
  error: null,

  setAccount: (account) => set({ account }),
  setOrders: (orders) => set({ orders }),
  addOrder: (order) => set((state) => ({ orders: [order, ...state.orders] })),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
}));

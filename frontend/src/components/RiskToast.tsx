/* ================================================================
   兼容垫片 —— 旧的 RiskToast API 现在转发到统一 Toast 系统
   （components/common/Toast.tsx）。保留导出名以免历史调用方断裂：
   - useRiskToastStore((s) => s.addToast)   // hook + selector
   - useRiskToastStore.getState().addToast  // 命令式
   新代码请直接用：import { toast } from './common/Toast'
   ================================================================ */
import React from 'react';
import { create } from 'zustand';
import { toast } from './common/Toast';

export interface RiskToastInput {
  rule: string;
  message: string;
  severity: 'WARNING' | 'ERROR';
}

interface RiskToastShim {
  addToast: (t: RiskToastInput) => void;
}

export const useRiskToastStore = create<RiskToastShim>(() => ({
  addToast: (t) =>
    toast.show({
      kind: t.severity === 'ERROR' ? 'error' : 'warning',
      title: t.rule,
      message: t.message,
    }),
}));

/** 渲染交由统一 <Toaster/>，此容器留空以免重复渲染 */
export const RiskToastContainer: React.FC = () => null;

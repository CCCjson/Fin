import React, { useState } from 'react';
import { Card } from '../common/Card';
import { CryptoPendingCard } from './CryptoPendingCard';
import { useCryptoStrategyStore } from '../../stores/cryptoStrategyStore';

export const CryptoPendingTab: React.FC = () => {
  const { pending, confirmPending, rejectPending } = useCryptoStrategyStore();
  const [busyRef, setBusyRef] = useState<string | null>(null);

  const handle = async (fn: (ref: string) => Promise<void>, ref: string) => {
    setBusyRef(ref);
    try { await fn(ref); } finally { setBusyRef(null); }
  };

  return (
    <Card className="p-4 md:p-5">
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-white font-medium text-lg">待确认单</h2>
        {pending.length > 0 && (
          <span className="text-xs text-gray-500">{pending.length} 条等你逐笔确认</span>
        )}
      </div>

      {pending.length === 0 ? (
        <div className="text-center py-16">
          <div className="text-4xl mb-3 opacity-40">📭</div>
          <p className="text-gray-500">暂无待确认单</p>
          <p className="text-gray-600 text-xs mt-1">
            引擎运行、策略命中时会自动排单到这里，等你确认才成交
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {pending.map((o) => (
            <CryptoPendingCard
              key={o.order_ref}
              order={o}
              busy={busyRef === o.order_ref}
              onConfirm={(ref) => handle(confirmPending, ref)}
              onReject={(ref) => handle(rejectPending, ref)}
            />
          ))}
        </div>
      )}
    </Card>
  );
};

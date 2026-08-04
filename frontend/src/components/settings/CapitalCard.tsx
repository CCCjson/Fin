import React, { useCallback, useEffect, useState } from 'react';
import { Card } from '../common/Card';
import { Button } from '../common/Button';
import { toast } from '../common/Toast';
import { settingsService, type MarketCapital } from '../../services/settingsService';

/**
 * 分市场本金（S5 03c，Jason 2026-08-04 拍板）。
 *
 * 🔴 **没有「总资金」那一行，是刻意的。** 四个市场是 CNY/HKD/USD/USDT，
 * 相加就是混币种 —— 「每市场独立本金、不折算汇率」是投资组合模块已拍的板。
 * ⛔ 别在这里加一个求和的合计数：它一旦出现在屏幕上，早晚会有人拿它当分母。
 *
 * 🔴 **留空 = 未配置 ≠ 0**：未配置的市场策略不跑（fail-closed），
 * 0 是「这个市场不投钱」。所以未配置显式标出来，⛔ 别显示成 0。
 *
 * ⚠️ 这块写的是**数据库**（`UserSettings`），不是 `.env` —— 与本页其余卡片
 * 不是同一个存储，所以单独一个组件而不是塞进 `GroupCard`。
 * ⛔ 键名/币种/中文名都由后端给，前端不抄。
 */

const NUM_RE = /^\d+(\.\d+)?$/;

export const CapitalCard: React.FC = () => {
  const [rows, setRows] = useState<MarketCapital[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);

  const load = useCallback(() => {
    setFailed(false);
    settingsService.getMarketCapitals()
      .then((r) => {
        setRows(r.markets);
        const d: Record<string, string> = {};
        for (const m of r.markets) d[m.key] = m.value === null ? '' : String(m.value);
        setDraft(d);
      })
      // 🔴 **失败态必须与「未配置」分开**：都渲染成「未配置」的话，接口挂了会显示成
      //    四个市场都没配 —— 而把这两类状态分开正是这张卡存在的全部意义。
      .catch(() => { setRows(null); setFailed(true); });
  }, []);

  useEffect(load, [load]);

  const save = async (key: string, label: string) => {
    const v = (draft[key] ?? '').trim();
    if (v !== '' && !NUM_RE.test(v)) {
      toast.error('请填数字，或留空表示还没配置', label);
      return;
    }
    setSaving(key);
    try {
      await settingsService.updateDbSetting(key, v);
      toast.success(v === '' ? '已清空（该市场策略不会跑）' : '已保存', label);
      load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || '保存失败', label);
    } finally {
      setSaving(null);
    }
  };

  return (
    <Card className="p-4 md:p-6">
      <div className="mb-3">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <span>💵</span> 分市场本金
        </h2>
        <p className="text-xs text-gray-500 mt-1 leading-relaxed">
          每个市场各算各的，<b>不折算汇率、不求和</b> —— 币种都不一样，加起来没有意义。
        </p>
        <p className="text-[11px] text-amber-400 mt-1.5">
          ⚠️ 留空 = <b>还没配置</b>，该市场的策略<b>不会跑</b>（这与填 0 是两回事，
          0 表示「这个市场不投钱」）。
        </p>
      </div>

      {failed ? (
        <div className="text-sm text-gray-400 py-2">
          读不到设置 —— <b>这不代表没配置</b>。
          <button onClick={load} className="ml-2 text-primary underline">重试</button>
        </div>
      ) : rows === null ? (
        <div className="text-sm text-gray-500 py-2">加载中…</div>
      ) : (
        <div>
          {rows.map((m) => {
            const cur = m.value === null ? '' : String(m.value);
            const dirty = (draft[m.key] ?? '') !== cur;
            return (
              <div key={m.key}
                className="flex flex-col md:flex-row md:items-center gap-2 py-2.5 border-b border-border last:border-0">
                <div className="md:w-64 shrink-0">
                  <div className="text-sm text-gray-200">{m.label}本金（{m.currency}）</div>
                  <div className="text-[10px] text-gray-600 font-mono">{m.key}</div>
                  <div className={`text-[10px] mt-0.5 ${
                    m.value === null ? 'text-amber-500' : 'text-gray-500'}`}>
                    {m.value === null
                      ? '未配置 · 该市场策略不跑'
                      : `当前 ${m.value} ${m.currency}`}
                  </div>
                </div>
                <div className="flex-1 flex items-center gap-2">
                  <input
                    type="text"
                    inputMode="decimal"
                    value={draft[m.key] ?? ''}
                    onChange={(e) => setDraft({ ...draft, [m.key]: e.target.value })}
                    placeholder="留空=未配置"
                    className="flex-1 px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary outline-none text-sm"
                  />
                  <Button size="sm" variant="subtle" onClick={() => save(m.key, m.label)}
                    loading={saving === m.key} disabled={!dirty}>
                    保存
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
};

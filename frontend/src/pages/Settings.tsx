import React, { useCallback, useEffect, useState } from 'react';
import { Card } from '../components/common/Card';
import { Button } from '../components/common/Button';
import { SkeletonCard } from '../components/common/Skeleton';
import { toast } from '../components/common/Toast';
import { settingsService } from '../services/settingsService';
import { CapitalCard } from '../components/settings/CapitalCard';
import type { SettingField, SettingGroup } from '../services/settingsService';

const GROUP_ICON: Record<string, string> = {
  ai: '🤖',
  network: '🌐',
  crypto: '🪙',
  secrets: '🔑',
};

// 这些项应为正整数，前端保存前校验
const NUMERIC_KEYS = new Set(['CRYPTO_UPDATE_INTERVAL_MIN', 'CRYPTO_DAILY_MAX_LOOKBACK']);

/** 单个配置项：输入 + 保存（脱敏字段留空=不改） */
const FieldRow: React.FC<{ field: SettingField; onSaved: () => void }> = ({ field, onSaved }) => {
  const [val, setVal] = useState(field.value ?? '');
  const [saving, setSaving] = useState(false);

  // schema 重载后同步外部值（非敏感字段）
  useEffect(() => {
    setVal(field.value ?? '');
  }, [field.value]);

  const isNumeric = NUMERIC_KEYS.has(field.key);
  const dirty = field.sensitive ? val.trim() !== '' : val !== (field.value ?? '');

  const save = async () => {
    if (field.sensitive && val.trim() === '') {
      toast.info('留空表示不改动', field.label);
      return;
    }
    if (isNumeric && !/^\d+$/.test(val.trim())) {
      toast.error('请填正整数', field.label);
      return;
    }
    setSaving(true);
    try {
      await settingsService.updateSetting(field.key, val);
      toast.success('已保存', field.label);
      if (field.sensitive) setVal(''); // 敏感字段存完清空，回到「留空=不改」
      onSaved(); // 重载 schema 刷新 is_set/preview
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || '保存失败', field.label);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col md:flex-row md:items-center gap-2 py-2.5 border-b border-border last:border-0">
      <div className="md:w-64 shrink-0">
        <div className="text-sm text-gray-200">{field.label}</div>
        <div className="text-[10px] text-gray-600 font-mono">{field.key}</div>
        {field.sensitive && (
          <div className="text-[10px] text-gray-500 mt-0.5">
            {field.is_set ? `已配置 · ${field.preview}` : '未配置'}
          </div>
        )}
      </div>
      <div className="flex-1 flex items-center gap-2">
        {field.type === 'select' ? (
          <select
            value={val}
            onChange={(e) => setVal(e.target.value)}
            className="flex-1 px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary outline-none text-sm"
          >
            {(field.options || []).map((o) => (
              <option key={o} value={o}>{o}</option>
            ))}
          </select>
        ) : (
          <input
            type={field.type === 'password' ? 'password' : isNumeric ? 'number' : 'text'}
            value={val}
            onChange={(e) => setVal(e.target.value)}
            placeholder={field.sensitive ? '留空=不改动' : ''}
            className="flex-1 px-3 py-2 bg-dark-light text-white rounded-lg border border-border focus:border-primary outline-none text-sm"
          />
        )}
        <Button size="sm" variant="subtle" onClick={save} loading={saving} disabled={!dirty}>
          保存
        </Button>
      </div>
    </div>
  );
};

const GroupCard: React.FC<{ group: SettingGroup; onSaved: () => void }> = ({ group, onSaved }) => (
  <Card className="p-4 md:p-6">
    <div className="mb-3">
      <h2 className="text-lg font-semibold text-white flex items-center gap-2">
        <span>{GROUP_ICON[group.group] || '⚙️'}</span> {group.title}
      </h2>
      {group.desc && <p className="text-xs text-gray-500 mt-1 leading-relaxed">{group.desc}</p>}
      {group.group === 'crypto' && (
        <p className="text-[11px] text-amber-400 mt-1.5">
          ⚠️ 币安 API 密钥（BINANCE_API_KEY / SECRET）出于安全不在此配置，请手动改 backend/.env
        </p>
      )}
    </div>
    <div>
      {group.fields.map((f) => (
        <FieldRow key={f.key} field={f} onSaved={onSaved} />
      ))}
    </div>
  </Card>
);

export const Settings: React.FC = () => {
  const [groups, setGroups] = useState<SettingGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await settingsService.getSchema();
      setGroups(res.groups);
      setError(null);
    } catch (e: any) {
      setError(e?.message || '加载设置失败');
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="min-h-screen bg-gradient-dark p-4 md:p-6 pb-20 md:pb-6">
      <div className="max-w-4xl mx-auto space-y-5">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold text-white">⚙️ 系统设置</h1>
          <p className="text-xs text-gray-500 mt-1">
            白名单配置，落盘 backend/.env 并热更新运行进程（个别项需重启后端生效）
          </p>
        </div>

        {error && (
          <Card className="p-4 border border-bull/40">
            <span className="text-bull text-sm">{error}</span>
            <button onClick={load} className="ml-3 text-primary text-sm underline">重试</button>
          </Card>
        )}

        {/* ⚠️ 本金写的是**数据库**（UserSettings），下面那些卡片写的是 .env ——
            两套存储，所以它自己加载自己的数据，不吃 groups/error。
            置顶是因为它是 fail-closed 的前提：没填就没有策略会跑。 */}
        <CapitalCard />

        {!groups && !error ? (
          <>
            <SkeletonCard />
            <SkeletonCard />
          </>
        ) : (
          groups?.map((g) => <GroupCard key={g.group} group={g} onSaved={load} />)
        )}
      </div>
    </div>
  );
};

export default Settings;

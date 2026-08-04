import api from './api';

export type SettingFieldType = 'text' | 'password' | 'select';

export interface SettingField {
  key: string;
  label: string;
  type: SettingFieldType;
  options?: string[];        // 仅 select
  value: string;             // 敏感字段恒为空（留空=不改）；非敏感=当前值
  sensitive?: boolean;
  is_set?: boolean;          // 敏感字段：当前 .env 是否已配
  preview?: string;          // 敏感字段：脱敏预览（不含真实值）
}

export interface SettingGroup {
  group: string;
  title: string;
  desc?: string | null;
  fields: SettingField[];
}

export interface SettingsSchema {
  groups: SettingGroup[];
}

export interface UpdateSettingResult {
  updated: string;
  label: string;
  note: string;
}

/**
 * ⛔ **前端不抄键名/币种/中文名** —— 全部由 `/data/market-capitals` 给。
 * 抄一份的话，加市场或改名时会分叉（`capital_*` 的键名此前已经有三处了）。
 * ⚠️ `value` 为 `null` = **未配置**（≠ 0）。
 */
export interface MarketCapital {
  market: string;
  label: string;      // 中文名（后端 MARKET_LABELS）
  currency: string;   // CNY / HKD / USD / USDT
  key: string;        // UserSettings 里的键
  value: number | null;
}

export const settingsService = {
  /** 分市场本金现状。⚠️ 与 getSchema 不是一个存储（这套在数据库里）。 */
  getMarketCapitals: (): Promise<{ markets: MarketCapital[] }> =>
    api.get('/data/market-capitals'),

  /**
   * 写数据库那套设置（本金/风控）。
   *
   * ⚠️ 与 `getSchema`/`updateSetting` 是**两个不同的存储**：`/settings/*` 写 `.env`，
   * 这条写数据库（`UserSettings`）。⛔ 别把资金搬去 .env —— 风控参数和 API 密钥
   * 不该同一个口子。
   * 🔴 传空串 = **清回未配置**（该市场策略不跑），与传 `"0"`（这个市场不投钱）
   * 是两件事。
   */
  updateDbSetting: (key: string, value: string): Promise<{ key: string; value: string }> =>
    api.put(`/data/settings/${encodeURIComponent(key)}`, null, { params: { value } }),

  /** 读取 schema + 当前值（敏感字段脱敏） */
  getSchema: (): Promise<SettingsSchema> => api.get('/settings/schema'),

  /** 保存单个配置项（白名单校验在后端；未知 key/非法值返 400） */
  updateSetting: (key: string, value: string): Promise<UpdateSettingResult> =>
    api.put(`/settings/${encodeURIComponent(key)}`, { value }),
};

export default settingsService;

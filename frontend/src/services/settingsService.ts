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

export const settingsService = {
  /** 读取 schema + 当前值（敏感字段脱敏） */
  getSchema: (): Promise<SettingsSchema> => api.get('/settings/schema'),

  /** 保存单个配置项（白名单校验在后端；未知 key/非法值返 400） */
  updateSetting: (key: string, value: string): Promise<UpdateSettingResult> =>
    api.put(`/settings/${encodeURIComponent(key)}`, { value }),
};

export default settingsService;

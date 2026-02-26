import api from './api';

const API_BASE = import.meta.env.VITE_API_URL || '/api';

// ==================== 类型定义 ====================

export interface ReportStreamEvent {
  event: 'collecting' | 'start' | 'chunk' | 'done' | 'error' | 'chapter_progress' | 'chapter_complete';
  // collecting
  message?: string;
  // start
  report_id?: string;
  title?: string;
  // chunk
  content?: string;
  // done
  token_count?: number;
  generation_time?: number;
  // error (also uses message)
  // chapter_progress
  call_index?: number;
  total_calls?: number;
  chapters?: number[];
  label?: string;
  // chapter_complete
  chapter_number?: number;
}

export interface ReportSummary {
  report_id: string;
  report_type: string;
  title: string;
  status: string;
  model_used: string;
  token_count: number | null;
  generation_time_seconds: number | null;
  period_start: string | null;
  period_end: string | null;
  created_at: string | null;
}

export interface ReportDetail extends ReportSummary {
  content: string | null;
  error_message: string | null;
}

export interface GenerateReportParams {
  report_type: string;
  model: string;
  period_end?: string;
}

// ==================== API ====================

export const reportService = {
  /**
   * 流式生成报告（NDJSON）
   */
  generateReport: async (
    params: GenerateReportParams,
    onEvent: (event: ReportStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const response = await fetch(`${API_BASE}/reports/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
      signal,
    });

    if (!response.ok) {
      throw new Error(`生成报告失败: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) throw new Error('无法获取响应流');

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event: ReportStreamEvent = JSON.parse(trimmed);
          onEvent(event);
        } catch (e) {
          console.warn('解析报告事件失败:', trimmed, e);
        }
      }
    }

    if (buffer.trim()) {
      try {
        const event: ReportStreamEvent = JSON.parse(buffer.trim());
        onEvent(event);
      } catch (e) {
        console.warn('解析最后的报告事件失败:', buffer, e);
      }
    }
  },

  /**
   * 获取报告列表
   */
  getReports: async (params?: {
    report_type?: string;
    limit?: number;
    offset?: number;
  }): Promise<{ total: number; reports: ReportSummary[] }> => {
    return api.get('/reports', { params });
  },

  /**
   * 获取单个报告详情
   */
  getReport: async (reportId: string): Promise<ReportDetail> => {
    return api.get(`/reports/${reportId}`);
  },

  /**
   * 删除报告
   */
  deleteReport: async (reportId: string): Promise<{ message: string }> => {
    return api.delete(`/reports/${reportId}`);
  },

  /**
   * 下载报告 PDF
   */
  downloadPdf: async (reportId: string, title?: string): Promise<void> => {
    const response = await fetch(`${API_BASE}/reports/${reportId}/pdf`);
    if (!response.ok) {
      const err = await response.json().catch(() => ({ detail: '下载失败' }));
      throw new Error(err.detail || '下载失败');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${title || reportId}.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },
};

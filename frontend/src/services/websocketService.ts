/**
 * WebSocket 服务 — 自动化交易实时推送
 *
 * 自动重连（指数退避），心跳保活，消息分发
 */
import { getAuthToken } from '../utils/authFetch';

type MessageHandler = (data: any) => void;

/** 给 WS URL 追加 ?token= 鉴权参数（浏览器 WS 不能带 header）。 */
function withToken(url: string): string {
  const token = getAuthToken();
  if (!token) return url;
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
}

class WebSocketService {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<MessageHandler>> = new Map();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private _connected = false;
  private _url = '';

  get connected(): boolean {
    return this._connected;
  }

  /**
   * 连接 WebSocket
   */
  connect(url?: string): void {
    if (this.ws && this._connected) return;

    const baseUrl = url || this._buildUrl();
    this._url = baseUrl;

    try {
      // 每次连接时取最新 token 拼上（重连也能拿到刷新后的 token）
      this.ws = new WebSocket(withToken(baseUrl));

      this.ws.onopen = () => {
        this._connected = true;
        this.reconnectAttempts = 0;
        console.log('[WS] 已连接');
        this._startHeartbeat();
        this._emit('connection', { connected: true });
      };

      this.ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          const type = msg.type || 'unknown';
          // 分发到对应 handler
          this._emit(type, msg.data || msg);
          // 同时分发到 * 全局监听
          this._emit('*', msg);
        } catch {
          console.warn('[WS] 解析消息失败:', event.data);
        }
      };

      this.ws.onclose = () => {
        this._connected = false;
        this._stopHeartbeat();
        console.log('[WS] 已断开');
        this._emit('connection', { connected: false });
        this._scheduleReconnect();
      };

      this.ws.onerror = (err) => {
        console.error('[WS] 错误:', err);
      };
    } catch (err) {
      console.error('[WS] 创建失败:', err);
      this._scheduleReconnect();
    }
  }

  /**
   * 断开连接
   */
  disconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this._stopHeartbeat();
    this.reconnectAttempts = this.maxReconnectAttempts; // 阻止自动重连
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._connected = false;
  }

  /**
   * 监听消息类型
   * @returns 取消监听函数
   */
  on(type: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(type)) {
      this.handlers.set(type, new Set());
    }
    this.handlers.get(type)!.add(handler);

    return () => {
      const set = this.handlers.get(type);
      if (set) {
        set.delete(handler);
        if (set.size === 0) this.handlers.delete(type);
      }
    };
  }

  // -------- 内部方法 --------

  private _buildUrl(): string {
    // VITE_API_URL 恒为本机后端绝对地址（见 frontend/.env），http → ws 换个 scheme 即可。
    // 旧版还有一条「相对路径 /api」的分支伺候 vite dev proxy，随 web 端退役一并删除
    // ——它本身也是坏的：'/api' 两次 replace 后变成空串，返回相对 URL 落到 dev server 上，
    // 而 vite 只 proxy 了 /api，automation 的 WS 从来没连通过。
    const apiUrl = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';
    return `${apiUrl.replace(/^http/, 'ws')}/ws/automation`;
  }

  private _emit(type: string, data: any): void {
    const set = this.handlers.get(type);
    if (set) {
      set.forEach((handler) => {
        try {
          handler(data);
        } catch (err) {
          console.error(`[WS] handler error (${type}):`, err);
        }
      });
    }
  }

  private _startHeartbeat(): void {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.ws && this._connected) {
        try {
          this.ws.send(JSON.stringify({ type: 'ping' }));
        } catch {
          // ignore
        }
      }
    }, 30000);
  }

  private _stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.warn('[WS] 达到最大重连次数');
      return;
    }

    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
    this.reconnectAttempts++;

    console.log(`[WS] ${delay / 1000}s 后尝试重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);

    this.reconnectTimer = setTimeout(() => {
      this.connect(this._url || undefined);
    }, delay);
  }
}

// 全局单例
const wsService = new WebSocketService();
export default wsService;

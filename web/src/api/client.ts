// 与 Django REST 后端 (/api/v1) 的唯一网络层。
// 沿用鸿蒙/小程序端契约：JWT Bearer、401 自动刷新一次、分页信封只在 ViewSet 列表。

const STORAGE_BASE = 'al_web_api_base';
const STORAGE_ACCESS = 'al_web_access';
const STORAGE_REFRESH = 'al_web_refresh';

// 生产同源部署时留空即可；本地 dev 走 vite proxy 的 /api。
const configured = (import.meta.env.VITE_API_BASE as string | undefined) || '';

export function apiBase(): string {
  const saved = localStorage.getItem(STORAGE_BASE);
  return (saved || configured || '/api/v1').replace(/\/+$/, '');
}

export function setApiBase(url: string): void {
  localStorage.setItem(STORAGE_BASE, url.trim().replace(/\/+$/, '') || '/api/v1');
}

export const token = () => localStorage.getItem(STORAGE_ACCESS) || '';
const refreshToken = () => localStorage.getItem(STORAGE_REFRESH) || '';

export function setSession(access: string, refresh: string): void {
  localStorage.setItem(STORAGE_ACCESS, access);
  localStorage.setItem(STORAGE_REFRESH, refresh);
}

export function clearSession(): void {
  localStorage.removeItem(STORAGE_ACCESS);
  localStorage.removeItem(STORAGE_REFRESH);
}

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

function parseError(data: unknown, status: number): string {
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>;
    if (typeof obj.detail === 'string') return obj.detail;
    const key = Object.keys(obj)[0];
    if (key) {
      const value = obj[key];
      if (Array.isArray(value)) return `${key}：${value.join('；')}`;
      if (typeof value === 'string') return `${key}：${value}`;
    }
    return JSON.stringify(obj);
  }
  return `${data ? String(data) : '请求失败'}（HTTP ${status}）`;
}

let refreshing: Promise<void> | null = null;

async function raw<T>(path: string, options: RequestInit & { auth?: boolean } = {}): Promise<T> {
  const url = /^https?:\/\//.test(path) ? path : `${apiBase()}${path}`;
  const headers = new Headers(options.headers || {});
  if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (options.auth !== false && token()) headers.set('Authorization', `Bearer ${token()}`);

  const res = await fetch(url, { ...options, headers });
  const text = await res.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // CSV / 纯文本保持原文
  }
  if (res.ok) return data as T;
  throw new ApiError(parseError(data, res.status), res.status, data);
}

export async function request<T = unknown>(
  path: string,
  options: RequestInit & { auth?: boolean } = {},
): Promise<T> {
  try {
    return await raw<T>(path, options);
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 401 || options.auth === false || !refreshToken()) {
      if (err instanceof ApiError && err.status === 401) redirectToLogin();
      throw err;
    }
    if (!refreshing) {
      refreshing = raw<{ access: string; refresh?: string }>('/auth/token/refresh/', {
        method: 'POST',
        auth: false,
        body: JSON.stringify({ refresh: refreshToken() }),
      })
        .then((data) => setSession(data.access, data.refresh || refreshToken()))
        .finally(() => {
          refreshing = null;
        });
    }
    try {
      await refreshing;
      return await raw<T>(path, options);
    } catch {
      clearSession();
      redirectToLogin();
      throw err;
    }
  }
}

function redirectToLogin(): void {
  if (location.pathname !== '/login') location.href = '/login';
}

export const api = {
  get: <T = unknown>(path: string) => request<T>(path),
  post: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T = unknown>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: <T = unknown>(path: string) => request<T>(path, { method: 'DELETE' }),

  async login(username: string, password: string) {
    const data = await request<{ access: string; refresh: string }>('/auth/token/', {
      method: 'POST',
      auth: false,
      body: JSON.stringify({ username, password }),
    });
    setSession(data.access, data.refresh);
    return data;
  },

  register(username: string, password: string, email?: string) {
    return request('/auth/register/', {
      method: 'POST',
      auth: false,
      body: JSON.stringify({ username, password, ...(email ? { email } : {}) }),
    });
  },

  async me<T = { id: number; username: string; email: string }>() {
    return request<T>('/auth/me/');
  },

  // multipart：截图识别 / CSV 文件导入
  async upload(path: string, file: File, field = 'file'): Promise<unknown> {
    const form = new FormData();
    form.append(field, file);
    const headers = new Headers();
    if (token()) headers.set('Authorization', `Bearer ${token()}`);
    const res = await fetch(`${apiBase()}${path}`, { method: 'POST', headers, body: form });
    const text = await res.text();
    let data: unknown = text;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      /* keep raw */
    }
    if (!res.ok) throw new ApiError(parseError(data, res.status), res.status, data);
    return data;
  },

  // CSV 导出：拿到原文后由浏览器下载
  async downloadCsv(path: string, filename: string): Promise<void> {
    const headers = new Headers();
    if (token()) headers.set('Authorization', `Bearer ${token()}`);
    const res = await fetch(`${apiBase()}${path}`, { headers });
    if (!res.ok) throw new ApiError('导出失败', res.status);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};

export function rows<T>(data: { results?: T[] } | T[] | null): T[] {
  if (!data) return [];
  return Array.isArray(data) ? data : data.results || [];
}

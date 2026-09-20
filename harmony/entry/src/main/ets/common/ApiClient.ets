import { http } from '@kit.NetworkKit';
import { preferences } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';

const TOKEN_KEY: string = 'jwt_access';
const STORE_NAME: string = 'asset_ledger';

/**
 * 默认后端地址 —— **全客户端只有这一处**。
 *
 * 它原先在 `ApiClient.getBaseUrl()` 和 `EntryAbility.onCreate()` 里各写了一遍，
 * 靠两条测试钉住「两份一致」（本机没有 DevEco / hvigor 工具链，改 `.ets` 编译不了，
 * 改坏了只有真机跑起来才知道）。现在 `EntryAbility` import 这个常量，
 * 「一致」不再需要被检查 —— 只有一处可改。
 */
export const DEFAULT_API_BASE_URL: string = 'http://10.0.2.2:8000/api/v1';

/** 后端接口封装：统一注入 JWT、解析响应、抛出可读错误。 */
export class ApiClient {
  static token: string = '';

  static getBaseUrl(): string {
    const url = AppStorage.get<string>('apiBaseUrl');
    return url ? url : DEFAULT_API_BASE_URL;
  }

  private static getStore(): preferences.Preferences | undefined {
    const context = AppStorage.get<common.UIAbilityContext>('abilityContext');
    if (!context) {
      return undefined;
    }
    return preferences.getPreferencesSync(context, { name: STORE_NAME });
  }

  /** 启动时恢复本地 token，避免每次重新登录。 */
  static async restore(): Promise<void> {
    try {
      const store = ApiClient.getStore();
      if (store) {
        ApiClient.token = store.getSync(TOKEN_KEY, '') as string;
      }
    } catch (err) {
      console.warn(`restore token failed: ${JSON.stringify(err)}`);
    }
  }

  private static saveToken(token: string): void {
    ApiClient.token = token;
    try {
      const store = ApiClient.getStore();
      if (store) {
        store.putSync(TOKEN_KEY, token);
        store.flushSync();
      }
    } catch (err) {
      console.warn(`save token failed: ${JSON.stringify(err)}`);
    }
  }

  static clear(): void {
    ApiClient.token = '';
    const store = ApiClient.getStore();
    if (store) {
      store.deleteSync(TOKEN_KEY);
      store.flushSync();
    }
  }

  static async login(username: string, password: string): Promise<void> {
    const data = await ApiClient.post<Record<string, string>>('/auth/token/', { username: username, password: password });
    ApiClient.saveToken(data['access']);
  }

  static async register(username: string, password: string): Promise<void> {
    await ApiClient.post<Record<string, string>>('/auth/register/', { username: username, password: password });
  }

  private static async send<T>(path: string, method: http.RequestMethod, body?: Object): Promise<T> {
    const request = http.createHttp();
    const header: Record<string, string> = { 'Content-Type': 'application/json' };
    if (ApiClient.token.length > 0) {
      header['Authorization'] = `Bearer ${ApiClient.token}`;
    }
    try {
      const response = await request.request(`${ApiClient.getBaseUrl()}${path}`, {
        method: method,
        header: header,
        extraData: body ? JSON.stringify(body) : undefined,
        expectDataType: http.HttpDataType.STRING,
        connectTimeout: 10000,
        readTimeout: 15000
      });
      const raw: string = typeof response.result === 'string' ? response.result : JSON.stringify(response.result);
      if (response.responseCode >= 400) {
        throw new Error(`HTTP ${response.responseCode}: ${raw}`);
      }
      return JSON.parse(raw) as T;
    } finally {
      request.destroy();
    }
  }

  static get<T>(path: string): Promise<T> {
    return ApiClient.send<T>(path, http.RequestMethod.GET);
  }

  static post<T>(path: string, body?: Object): Promise<T> {
    return ApiClient.send<T>(path, http.RequestMethod.POST, body);
  }
}

/** 数字格式化：统一保留两位小数并加千分位。
 *
 * 参数收 `null`：后端有一批字段「算不出来」时给的是 `null`（不是 0）——
 * 股息率在成本为 0（已清仓）时就是 `null`，而 `null` 与 `0` 对读者是两件事。
 */
export function fmt(value: string | null | undefined, digits: number = 2): string {
  if (!value) {
    return '-';
  }
  const num = parseFloat(value);
  if (isNaN(num)) {
    return '-';
  }
  return num.toFixed(digits);
}

/** 涨跌颜色：中国习惯，涨红跌绿。参数收 `null` 的理由同 `fmt`。 */
export function pnlColor(value: string | null | undefined): string {
  if (!value) {
    return '#8A8A8A';
  }
  const num = parseFloat(value);
  if (isNaN(num) || num === 0) {
    return '#8A8A8A';
  }
  return num > 0 ? '#E24B4A' : '#1D9E75';
}

/**
 * 比率 → 百分数文本。**全客户端只有这一处**做这件事。
 *
 * 它原先散在三处各写一遍 `(x * 100).toFixed(2) + '%'`（统计页的年化、持仓页的年化、
 * 收件箱的置信度），而现在股息率要加进来会变成第四处 —— 同一件事写四遍必然漂移。
 *
 * `null` / `undefined` / 空串 / 非数都返回 `'-'`：后端「算不出来」给的就是 `null`，
 * 显示成 `0.00%` 会把「没有数据」说成「收益为零」。**`0` 本身照常显示 `0.00%`**。
 */
export function fmtPercent(value: string | number | null | undefined, digits: number = 2): string {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const num = typeof value === 'number' ? value : parseFloat(value);
  if (isNaN(num)) {
    return '-';
  }
  return `${(num * 100).toFixed(digits)}%`;
}

/** 有没有一个「真的值得显示」的金额（空、非数、0 都算没有）。 */
export function hasAmount(value: string | null | undefined): boolean {
  if (!value) {
    return false;
  }
  const num = parseFloat(value);
  return !isNaN(num) && num !== 0;
}

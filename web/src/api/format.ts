// 纯展示层格式化；金额口径全部来自后端，不在前端重算。

export const num = (v: unknown, digits = 2): string => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return '—';
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
};

export const money = (v: unknown, digits = 2) => num(v, digits);

export const pct = (v: unknown, digits = 2): string => {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return '—';
  return `${num(n * 100, digits)}%`;
};

// A 股习惯：红涨绿跌
export const trendClass = (v: unknown): string => {
  const n = Number(v);
  if (Number.isNaN(n) || n === 0) return '';
  return n > 0 ? 'up' : 'down';
};

export const date = (v: unknown): string => (v ? String(v).slice(0, 10) : '—');
export const datetime = (v: unknown): string => (v ? String(v).slice(0, 16).replace('T', ' ') : '—');

export const today = (): string => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

export const localDateTime = (): string => {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${today()}T${pad(d.getHours())}:${pad(d.getMinutes())}:00`;
};

export const year = () => new Date().getFullYear();

export const SIDE_LABELS: Record<string, string> = {
  BUY: '买入', SELL: '卖出', DIVIDEND: '股息', DEPOSIT: '入金',
  WITHDRAW: '出金', FEE: '费用', TAX: '税费', SPLIT: '拆分',
};
export const MARKET_LABELS: Record<string, string> = {
  A: 'A股', HK: '港股', US: '美股', FUND: '基金',
  CRYPTO: '数字货币', FOREX: '外汇', OTHER: '其他',
};
export const ACCOUNT_TYPE_LABELS: Record<string, string> = {
  BROKER: '券商', BANK: '银行', CRYPTO: '交易所/钱包', CASH: '现金', OTHER: '其他',
};
export const ASSET_TYPE_LABELS: Record<string, string> = {
  STOCK: '股票', ETF: 'ETF', BOND: '债券', FUND: '基金',
  CRYPTO: '加密', FOREX: '外汇', CASH: '现金', OTHER: '其他',
};

export const SIDES = ['BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW'];
export const MARKETS = Object.keys(MARKET_LABELS);
export const CURRENCIES = ['CNY', 'USD', 'HKD', 'USDT', 'EUR', 'JPY'];
export const ACCOUNT_TYPES = Object.keys(ACCOUNT_TYPE_LABELS);
export const ASSET_TYPES = Object.keys(ASSET_TYPE_LABELS);

export const sideLabel = (v: string) => SIDE_LABELS[v] || v || '—';
export const marketLabel = (v: string) => MARKET_LABELS[v] || v || '—';
export const currencyForMarket = (m: string) =>
  m === 'HK' ? 'HKD' : m === 'US' ? 'USD' : m === 'CRYPTO' ? 'USDT' : 'CNY';

// 后端真实字段的 TypeScript 镜像（Decimal 以字符串返回）。

export interface Account {
  id: number;
  name: string;
  broker: string;
  market: string;
  account_type: string;
  currency: string;
  note: string;
}

export interface Asset {
  id: number;
  symbol: string;
  name: string;
  market: string;
  asset_type: string;
  currency: string;
  is_dividend_asset: boolean;
  note: string;
}

export interface Transaction {
  id: number;
  account: number;
  asset: number | null;
  side: string;
  quantity: string | null;
  price: string | null;
  amount: string;
  fee: string;
  tax: string;
  currency: string;
  traded_at: string;
  source: string;
  note: string;
  client_request_id: string | null;
}

export interface DividendRecord {
  id: number;
  asset: number;
  account: number | null;
  pay_date: string | null;
  ex_date: string | null;
  gross: string;
  tax: string;
  net: string;
  currency: string;
  reinvested: boolean;
}

export interface Position {
  account_id: number;
  account_name: string;
  asset_id: number;
  symbol: string;
  name: string;
  market: string;
  currency: string;
  quantity: string;
  avg_cost: string;
  cost_basis: string;
  realized_pnl: string;
  dividend_total: string;
  annual_dividend: string;
  dividend_yield: string | null;
  last_price: string | null;
  market_value: string | null;
  unrealized_pnl: string | null;
}

export interface Summary {
  base_currency: string;
  market_value: string;
  unpriced_cost_basis: string;
  unpriced_symbols: string[];
  cost_basis: string;
  unrealized_pnl: string;
  realized_pnl: string;
  dividend_total: string;
  dividend_annual: string;
  dividend_yield: string | null;
  monthly_passive_income: string | null;
  total_pnl: string;
  annualized: number | null;
  valuation_note: string | null;
  fx_warning: boolean;
  position_count: number;
}

export interface Draft {
  id: number;
  kind: string;
  status: string;
  confidence: number;
  missing: string[];
  result: Record<string, unknown>;
  error: string | null;
  evidence_url: string | null;
  raw_text: string;
  confirmed_at: string | null;
  created_at: string;
}

export interface CalendarEntry {
  asset_id: number;
  account_id: number;
  currency: string;
  amount: string;
  pay_date: string | null;
  ex_date: string | null;
  days_until: number | null;
  origin: string;
  symbol: string;
  asset_name: string;
  account_name: string;
}

export interface CalendarData {
  as_of: string;
  upcoming: CalendarEntry[];
  received: CalendarEntry[];
  undated: CalendarEntry[];
  totals: {
    count: number;
    next_pay_date: string | null;
    by_currency: Record<string, string>;
    [key: string]: unknown;
  };
}

export interface Quote {
  asset_id: number;
  symbol: string;
  name: string;
  price: string | null;
  currency?: string;
  change_pct?: number;
  fetched_at?: string;
  source?: string;
  stale: boolean;
}

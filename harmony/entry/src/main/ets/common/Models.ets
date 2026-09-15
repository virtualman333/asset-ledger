export interface Position {
  asset_id: number;
  symbol: string;
  name: string;
  market: string;
  currency: string;
  quantity: string;
  avg_cost: string;
  cost_basis: string;
  last_price: string;
  market_value: string;
  unrealized_pnl: string;
  realized_pnl: string;
  dividend_total: string;
}

export interface Summary {
  base_currency: string;
  market_value: string;
  cost_basis: string;
  unrealized_pnl: string;
  realized_pnl: string;
  dividend_total: string;
  total_pnl: string;
  annualized: number;
  annualized_note: string;
  fx_warning: boolean;
  position_count: number;
}

export interface DraftResult {
  side: string;
  symbol: string;
  market: string;
  name: string;
  quantity: string;
  price: string;
  fee: string;
  currency: string;
  traded_at: string;
  confidence: number;
  missing: string[];
}

export interface Draft {
  id: number;
  kind: string;
  status: string;
  confidence: number;
  missing: string[];
  result: DraftResult;
  evidence_url: string;
  raw_text: string;
  created_at: string;
}

export interface DividendMonth {
  month: string;
  net: string;
  gross: string;
  tax: string;
  count: number;
}

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
  /** 只含**拿到报价**的持仓。拿不到报价的那部分在 unpriced_cost_basis 里 */
  market_value: string;
  /** 有数量但拿不到报价的持仓成本（已折算到基准货币） */
  unpriced_cost_basis: string;
  /** 上面那些持仓的标的代码，用来告诉用户是哪几个 */
  unpriced_symbols: string[];
  cost_basis: string;
  unrealized_pnl: string;
  realized_pnl: string;
  dividend_total: string;
  total_pnl: string;
  annualized: number;
  /** 一句话解释上面的数字：估值有没有缺口、年化为什么算不出来。null 表示一切正常 */
  valuation_note: string;
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

export interface Position {
  /** 同一标的可能同时在两个账户里持有 —— 列表要按「账户 + 标的」才分得开 */
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
  /** 拿不到报价时是 null（后端不给 0 —— 页面上那一格显示 '-'） */
  last_price: string | null;
  /** 没有报价就算不出市值，后端给的是 null 而不是 0 */
  market_value: string | null;
  /** 同上：没报价时浮盈也算不出来 */
  unrealized_pnl: string | null;
  realized_pnl: string;
  dividend_total: string;
  /** 近 365 天这只标的的股息 —— 后端每一行都算了，客户端此前连声明都没有 */
  annual_dividend: string;
  /** 这只标的的股息率 = 近一年股息 ÷ 成本；成本为 0（已清仓）时是 null 而不是 0 */
  dividend_yield: string | null;
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
  /** 近 365 天股息 —— 用滚动一年而不是自然年，免得 12 月建的账本次年 1 月显示成 0 */
  dividend_annual: string;
  /** 股息率 = dividend_annual ÷ 持仓成本；成本为 0（已清仓）时是 null 而不是 0 */
  dividend_yield: string | null;
  /** 月度被动收入 = dividend_annual ÷ 12 */
  monthly_passive_income: string | null;
  total_pnl: string;
  /** XIRR 年化。现金流不够时算不出来，后端给的是 null（不是 0） */
  annualized: number | null;
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

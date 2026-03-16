export interface OverviewData {
  paper_balance: number;
  total_equity: number;
  unrealized_pnl: number;
  realized_pnl: number;
  open_positions: number;
  total_signals: number;
  max_drawdown: number;
  demo_mode: boolean;
}

export interface WalletScore {
  composite: number | null;
  copyability: number | null;
  roi: number | null;
  hit_rate: number | null;
  max_drawdown: number | null;
  classification: string | null;
  copy_decay_curve: Record<string, number>;
  scored_at: string | null;
}

export interface WalletSummary {
  id: string;
  address: string;
  label: string | null;
  is_tracked: boolean;
  score: WalletScore | null;
  live_status?: {
    last_trade_at: string | null;
    activity_label: 'ACTIVE' | 'WARM' | 'DORMANT' | 'UNKNOWN';
  };
  copy_performance?: {
    copied_trade_count: number;
    copied_realized_pnl: number;
    copied_win_rate: number | null;
    copied_avg_pnl: number | null;
    last_copied_at: string | null;
  };
}

export interface Signal {
  id: string;
  strategy: string;
  source_type: string;
  market_id: string | null;
  side: string;
  model_probability: number | null;
  model_confidence: number | null;
  market_price: number | null;
  raw_edge: number | null;
  net_edge: number | null;
  spread_z_score: number | null;
  costs_breakdown: Record<string, number>;
  created_at: string;
}

export interface PaperTrade {
  id: string;
  market_id: string;
  strategy: string;
  side: string;
  outcome: string;
  avg_entry_price: number | null;
  avg_exit_price: number | null;
  total_size: number | null;
  realized_pnl: number | null;
  unrealized_pnl: number | null;
  total_fees: number | null;
  total_slippage: number | null;
  status: string;
  exit_reason: string | null;
  opened_at: string | null;
  closed_at: string | null;
  target_structure: string | null;
  position_group_id: string | null;
  leg_index: number;
}

export interface EquityPoint {
  time: string;
  equity: number;
  cash: number;
  drawdown: number;
}

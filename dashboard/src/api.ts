// Fetch helper — every request carries the Bearer token from localStorage.

const TOKEN_KEY = "zetryn_dashboard_token";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string): Promise<T> {
  const res = await fetch(path, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return (await res.json()) as T;
}

// ── response shapes (mirror zetryn_bot/api/app.py) ──────────────────────────

export interface OpenPosition {
  mint: string;
  symbol: string;
  size_sol: number;
  confidence: number;
  opened_at: string;
  status: string;
  execution_mode: string;
}

export interface Overview {
  open_positions: OpenPosition[];
  today_pnl_sol: number;
  circuit_breaker: { limit_sol: number; tripped: boolean };
  closed_count: number;
  total_pnl_sol: number;
  win_rate: number;
}

export interface AiActivityRow {
  ts: string;
  mint: string;
  symbol: string;
  source: string;
  route: string;
  action: string;
  confidence: number;
  final_score: number;
  scores: Record<string, number>;
  reasoning: string;
  reasons: string[];
  outcome: string;
  outcome_detail: string;
}

export interface Trade {
  mint: string;
  symbol: string;
  size_sol: number;
  exit_sol: number;
  pnl_sol: number;
  reason: string;
  confidence: number;
  opened_at: string;
  closed_at: string;
  held_minutes: number;
}

export interface TradesPage {
  total: number;
  trades: Trade[];
}

export interface StatGroup {
  trades: number;
  wins: number;
  win_rate: number;
  pnl_sol: number;
  [key: string]: string | number;
}

export interface Stats {
  by_reason: StatGroup[];
  by_confidence: StatGroup[];
  by_day: StatGroup[];
}

export interface EquityPoint {
  ts: string;
  equity_sol: number;
}

export interface Status {
  db_ok: boolean;
  latest_ai_activity: string | null;
  latest_trade: string | null;
  bot_version: string;
  execution_mode: string;
}

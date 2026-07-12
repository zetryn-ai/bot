// Small shared pieces: stat tile, outcome/route badges (icon + label — never
// color alone), polling hook, relative-time helper.

import { useEffect, useState } from "react";
import { api } from "../api";

export function usePoll<T>(path: string, intervalMs = 5000): { data: T | null; error: string } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const d = await api<T>(path);
        if (alive) {
          setData(d);
          setError("");
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [path, intervalMs]);
  return { data, error };
}

export function StatTile({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "pos" | "neg";
}) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={`value mono ${tone ?? ""}`}>{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

// ── outcome (how far a token got after its AI verdict) ──────────────────────

export const OUTCOME_INFO: Record<
  string,
  { icon: string; label: string; cls: string; explain: string }
> = {
  opened: {
    icon: "●",
    label: "OPENED",
    cls: "opened",
    explain: "Passed every gate — a position was opened for this token.",
  },
  ai_skip: {
    icon: "—",
    label: "AI skip",
    cls: "",
    explain: "The AI analyst scored this token too low to act on. It stopped here.",
  },
  ai_abort: {
    icon: "✕",
    label: "AI abort",
    cls: "bad",
    explain: "The AI analysis errored or was aborted — treated as a conservative skip.",
  },
  rule_skip: {
    icon: "—",
    label: "rule skip",
    cls: "",
    explain:
      "The route's rule agent (no LLM) rejected this token — see the reasons for which rule fired.",
  },
  rule_abort: {
    icon: "✕",
    label: "rule abort",
    cls: "bad",
    explain: "The route's rule agent aborted on a hard safety signal (e.g. rug risk).",
  },
  not_buy_action: {
    icon: "◌",
    label: "watch only",
    cls: "",
    explain:
      "The AI verdict (watch/alert) is not in the configured buy actions, so no trade was attempted.",
  },
  cooldown: {
    icon: "⏸",
    label: "cooldown",
    cls: "warn",
    explain:
      "Re-entry cooldown: the bot closed a trade on this token recently (4h window), so it refuses to buy it again yet. This prevents churn — repeatedly re-buying the same falling token.",
  },
  already_held: {
    icon: "≡",
    label: "held",
    cls: "",
    explain: "A position in this token is already open — the bot never stacks positions.",
  },
  risk_rejected: {
    icon: "⛔",
    label: "risk gate",
    cls: "warn",
    explain: "A RiskManager gate rejected the trade — the detail names the exact gate.",
  },
  buy_failed: {
    icon: "!",
    label: "buy failed",
    cls: "bad",
    explain: "Risk approved the trade but the swap could not be executed (e.g. no route/quote).",
  },
};

export function outcomeInfo(outcome: string) {
  return (
    OUTCOME_INFO[outcome] ?? {
      icon: "…",
      label: outcome || "pending",
      cls: "",
      explain: outcome
        ? ""
        : "Decision recorded — the execution outcome has not been reported yet.",
    }
  );
}

export function OutcomeBadge({ outcome, detail }: { outcome: string; detail: string }) {
  const b = outcomeInfo(outcome);
  const title = [b.explain, detail].filter(Boolean).join("\n\n");
  return (
    <span className={`badge ${b.cls}`} title={title}>
      {b.icon} {b.label}
    </span>
  );
}

// ── route (which entry strategy handled the token) ──────────────────────────

export const ROUTE_INFO: Record<string, { icon: string; explain: string }> = {
  sniper: {
    icon: "⚡",
    explain: "Sniper route — fresh pump.fun launches, pure-rule decision (no LLM).",
  },
  graduation: {
    icon: "🎓",
    explain: "Graduation route — pump.fun → Raydium migrations.",
  },
  scanner: {
    icon: "🔍",
    explain: "Scanner route — generalist AI analyst for trending/boost/new-pool signals.",
  },
};

export function RouteBadge({ route }: { route: string }) {
  if (!route) return <span className="muted">—</span>;
  const r = ROUTE_INFO[route];
  return (
    <span className="badge route" title={r?.explain ?? `Route: ${route}`}>
      {r?.icon ?? "◆"} {route}
    </span>
  );
}

export function PnlText({ v, digits = 4 }: { v: number; digits?: number }) {
  return (
    <span className={`mono ${v > 0 ? "pos" : v < 0 ? "neg" : "muted"}`}>
      {v >= 0 ? "+" : ""}
      {v.toFixed(digits)}
    </span>
  );
}

export function ago(iso: string | null): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
  return `${(s / 86400).toFixed(1)}d ago`;
}

export function fmtWhen(iso: string | null): string {
  if (!iso) return "—";
  return `${new Date(iso).toLocaleString()} (${ago(iso)})`;
}

export function fmtDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

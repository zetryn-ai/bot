// Small shared pieces: stat tile, outcome badge (icon + label — never color
// alone), polling hook.

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

const OUTCOME_BADGE: Record<string, { icon: string; label: string; cls: string }> = {
  opened: { icon: "●", label: "OPENED", cls: "opened" },
  ai_skip: { icon: "—", label: "AI skip", cls: "" },
  ai_abort: { icon: "✕", label: "AI abort", cls: "bad" },
  not_buy_action: { icon: "◌", label: "watch only", cls: "" },
  cooldown: { icon: "⏸", label: "cooldown", cls: "warn" },
  already_held: { icon: "≡", label: "held", cls: "" },
  risk_rejected: { icon: "⛔", label: "risk gate", cls: "warn" },
  buy_failed: { icon: "!", label: "buy failed", cls: "bad" },
};

export function OutcomeBadge({ outcome, detail }: { outcome: string; detail: string }) {
  const b = OUTCOME_BADGE[outcome] ?? { icon: "…", label: outcome || "pending", cls: "" };
  return (
    <span className={`badge ${b.cls}`} title={detail}>
      {b.icon} {b.label}
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
  if (s < 60) return `${Math.floor(s)}s lalu`;
  if (s < 3600) return `${Math.floor(s / 60)}m lalu`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h lalu`;
  return `${(s / 86400).toFixed(1)}d lalu`;
}

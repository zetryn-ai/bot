import { useState } from "react";
import type { AiActivityRow, OpenPosition, Overview as OverviewData } from "../api";
import { OutcomeBadge, RouteBadge, StatTile, ago, usePoll } from "../components/bits";
import { AiDecisionModal, PositionModal } from "../components/modal";

function AiActivityTable({ rows }: { rows: AiActivityRow[] }) {
  const [selected, setSelected] = useState<AiActivityRow | null>(null);
  if (!rows.length) return <p className="muted">No AI decisions recorded yet.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Time</th>
            <th>Token</th>
            <th>Route</th>
            <th>Source</th>
            <th>Action</th>
            <th className="num">Conf</th>
            <th>Scores</th>
            <th>Outcome</th>
            <th>AI Reasoning</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={`${r.mint}-${r.ts}-${i}`}
              className="clickable"
              onClick={() => setSelected(r)}
            >
              <td className="muted mono">{ago(r.ts)}</td>
              <td className="mono token-cell">{r.symbol || r.mint.slice(0, 6)}</td>
              <td>
                <RouteBadge route={r.route} />
              </td>
              <td className="secondary">{r.source}</td>
              <td className="mono">{r.action}</td>
              <td className="num mono">{r.confidence.toFixed(2)}</td>
              <td>
                <span className="scorebar">
                  {(["safety", "market", "wallets", "social"] as const).map((k) =>
                    r.scores[k] !== undefined ? (
                      <span key={k} className="dim" title={`${k}: ${r.scores[k].toFixed(2)}`}>
                        {k[0].toUpperCase()}
                        <i style={{ "--w": `${Math.round(r.scores[k] * 100)}%` } as React.CSSProperties} />
                      </span>
                    ) : null,
                  )}
                </span>
              </td>
              <td>
                <OutcomeBadge outcome={r.outcome} detail={r.outcome_detail} />
              </td>
              <td>
                {r.reasoning ? (
                  <span className="reason-preview">{r.reasoning}</span>
                ) : r.reasons?.length ? (
                  <span className="reason-preview">{r.reasons.join(" · ")}</span>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected && <AiDecisionModal row={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function PnlBar({ pos }: { pos: OpenPosition }) {
  // Center = entry. Fill grows RIGHT toward TP (green) or LEFT toward SL
  // (red); neutral gray notch when flat or unmarked. Half-width is scaled to
  // the position's own TP/SL targets, so a full bar = target reached.
  const pnl = pos.unrealized_pnl_pct;
  const tp = Math.max(pos.take_profit_pct, 0.0001);
  const sl = Math.max(Math.abs(pos.stop_loss_pct), 0.0001);
  const stale = !pos.marked_at || Date.now() - new Date(pos.marked_at).getTime() > 120_000;
  const flat = pnl === null || stale || Math.abs(pnl) < 0.001;
  const pct = pnl ?? 0;
  const width = flat ? 0 : Math.min(1, Math.abs(pct) / (pct >= 0 ? tp : sl)) * 50;
  return (
    <div className="pnlbar-wrap">
      <div className="pnlbar-labels">
        <span className="neg mono">−{(sl * 100).toFixed(0)}% SL</span>
        <span
          className={`pnlbar-value mono ${flat ? "muted" : pct > 0 ? "pos" : "neg"}`}
          title={stale ? "waiting for the next price mark" : "live unrealized PnL"}
        >
          {pnl === null || stale ? "…" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`}
        </span>
        <span className="pos mono">+{(tp * 100).toFixed(0)}% TP</span>
      </div>
      <div className="pnlbar">
        {!flat && pct < 0 && (
          <div className="fill loss" style={{ width: `${width}%`, right: "50%" }} />
        )}
        {!flat && pct > 0 && (
          <div className="fill gain" style={{ width: `${width}%`, left: "50%" }} />
        )}
        <div className={`center-notch ${flat ? "idle" : ""}`} />
      </div>
    </div>
  );
}

function PositionsGrid({ positions }: { positions: OpenPosition[] }) {
  const [selected, setSelected] = useState<OpenPosition | null>(null);
  if (!positions.length) return <p className="muted">No open positions.</p>;
  return (
    <>
      <div className="positions-grid">
        {positions.map((p) => (
          <div key={p.mint} className="position-card clickable" onClick={() => setSelected(p)}>
            <div className="pos-head">
              <span className="mono token-cell">{p.symbol || p.mint.slice(0, 8)}</span>
              <RouteBadge route={p.route} />
              {p.partials.length > 0 && (
                <span className="badge good" title="Part of this position was already sold at a TP rung">
                  💰 partial ×{p.partials.length}
                </span>
              )}
              <span className="pos-age muted">{ago(p.opened_at)}</span>
            </div>
            <PnlBar pos={p} />
            <div className="pos-meta muted">
              <span className="mono">{p.size_sol.toFixed(4)} SOL</span>
              <span>conf <span className="mono">{p.confidence.toFixed(2)}</span></span>
              <span>{p.execution_mode}</span>
              <span>{p.status}</span>
            </div>
          </div>
        ))}
      </div>
      {selected && <PositionModal pos={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

export default function Overview() {
  const { data: ov, error } = usePoll<OverviewData>("/api/overview", 5000);
  const { data: activity } = usePoll<AiActivityRow[]>("/api/ai-activity?limit=50", 5000);

  if (error) return <p className="neg">Failed to load: {error}</p>;
  if (!ov) return <p className="muted">Loading…</p>;

  return (
    <>
      <div className="tiles">
        <StatTile
          label="PnL today"
          value={`${ov.today_pnl_sol >= 0 ? "+" : ""}${ov.today_pnl_sol.toFixed(4)} SOL`}
          tone={ov.today_pnl_sol > 0 ? "pos" : ov.today_pnl_sol < 0 ? "neg" : undefined}
        />
        <StatTile label="Open positions" value={String(ov.open_positions.length)} />
        <StatTile
          label="Win rate"
          value={`${Math.round(ov.win_rate * 100)}%`}
          sub={`${ov.closed_count} closed trades`}
        />
        <StatTile
          label="Total PnL"
          value={`${ov.total_pnl_sol >= 0 ? "+" : ""}${ov.total_pnl_sol.toFixed(4)} SOL`}
          tone={ov.total_pnl_sol > 0 ? "pos" : ov.total_pnl_sol < 0 ? "neg" : undefined}
        />
        <StatTile
          label="Circuit breaker"
          value={ov.circuit_breaker.tripped ? "⛔ TRIPPED" : "✓ safe"}
          sub={`limit ${ov.circuit_breaker.limit_sol} SOL/day`}
          tone={ov.circuit_breaker.tripped ? "neg" : undefined}
        />
      </div>

      <div className="card">
        <h2>
          Open positions <span className="hint">click a row for details</span>
        </h2>
        <PositionsGrid positions={ov.open_positions} />
      </div>

      <div className="card">
        <h2>
          <span className="live-dot" /> Live AI Activity{" "}
          <span className="hint">click a row for the full decision</span>
        </h2>
        <AiActivityTable rows={activity ?? []} />
      </div>
    </>
  );
}

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
            <th>Strategy</th>
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

function PositionsTable({ positions }: { positions: OpenPosition[] }) {
  const [selected, setSelected] = useState<OpenPosition | null>(null);
  if (!positions.length) return <p className="muted">No open positions.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Token</th>
            <th>Strategy</th>
            <th className="num">Size (SOL)</th>
            <th className="num">Conf</th>
            <th>Opened</th>
            <th>Mode</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.mint} className="clickable" onClick={() => setSelected(p)}>
              <td className="mono token-cell" title={p.mint}>
                {p.symbol || p.mint.slice(0, 8)}
              </td>
              <td>
                <RouteBadge route={p.route} />
              </td>
              <td className="num mono">{p.size_sol.toFixed(4)}</td>
              <td className="num mono">{p.confidence.toFixed(2)}</td>
              <td className="muted">{ago(p.opened_at)}</td>
              <td>{p.execution_mode}</td>
              <td>{p.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected && <PositionModal pos={selected} onClose={() => setSelected(null)} />}
    </div>
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
        <PositionsTable positions={ov.open_positions} />
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

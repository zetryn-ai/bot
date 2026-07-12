import type { AiActivityRow, Overview as OverviewData } from "../api";
import { OutcomeBadge, PnlText, StatTile, ago, usePoll } from "../components/bits";

function AiActivityTable({ rows }: { rows: AiActivityRow[] }) {
  if (!rows.length) return <p className="muted">Belum ada keputusan AI terekam.</p>;
  return (
    <div className="tablewrap">
      <table>
        <thead>
          <tr>
            <th>Waktu</th>
            <th>Token</th>
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
            <tr key={`${r.mint}-${r.ts}-${i}`}>
              <td className="muted mono">{ago(r.ts)}</td>
              <td>
                <span className="mono">{r.symbol || r.mint.slice(0, 6)}</span>
                {r.route && <span className="muted"> · {r.route}</span>}
              </td>
              <td className="secondary">{r.source}</td>
              <td className="mono">{r.action}</td>
              <td className="num mono">{r.confidence.toFixed(2)}</td>
              <td>
                <span className="scorebar">
                  {(["safety", "market", "wallets", "social"] as const).map((k) =>
                    r.scores[k] !== undefined ? (
                      <span key={k} title={k}>
                        {k[0].toUpperCase()}
                        {Math.round(r.scores[k] * 10)}
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
                  <details className="reasoning-toggle">
                    <summary>lihat</summary>
                    <div className="reasoning">{r.reasoning}</div>
                  </details>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Overview() {
  const { data: ov, error } = usePoll<OverviewData>("/api/overview", 5000);
  const { data: activity } = usePoll<AiActivityRow[]>("/api/ai-activity?limit=50", 5000);

  if (error) return <p className="neg">Gagal memuat: {error}</p>;
  if (!ov) return <p className="muted">Memuat…</p>;

  return (
    <>
      <div className="tiles">
        <StatTile
          label="PnL hari ini"
          value={`${ov.today_pnl_sol >= 0 ? "+" : ""}${ov.today_pnl_sol.toFixed(4)} SOL`}
          tone={ov.today_pnl_sol > 0 ? "pos" : ov.today_pnl_sol < 0 ? "neg" : undefined}
        />
        <StatTile label="Posisi terbuka" value={String(ov.open_positions.length)} />
        <StatTile
          label="Win rate"
          value={`${Math.round(ov.win_rate * 100)}%`}
          sub={`${ov.closed_count} trade tertutup`}
        />
        <StatTile
          label="Total PnL"
          value={`${ov.total_pnl_sol >= 0 ? "+" : ""}${ov.total_pnl_sol.toFixed(4)} SOL`}
          tone={ov.total_pnl_sol > 0 ? "pos" : ov.total_pnl_sol < 0 ? "neg" : undefined}
        />
        <StatTile
          label="Circuit breaker"
          value={ov.circuit_breaker.tripped ? "⛔ TRIPPED" : "✓ aman"}
          sub={`limit ${ov.circuit_breaker.limit_sol} SOL/hari`}
          tone={ov.circuit_breaker.tripped ? "neg" : undefined}
        />
      </div>

      <div className="card">
        <h2>Posisi terbuka</h2>
        {ov.open_positions.length === 0 ? (
          <p className="muted">Tidak ada posisi terbuka.</p>
        ) : (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Token</th>
                  <th className="num">Size (SOL)</th>
                  <th className="num">Conf</th>
                  <th>Dibuka</th>
                  <th>Mode</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {ov.open_positions.map((p) => (
                  <tr key={p.mint}>
                    <td className="mono" title={p.mint}>
                      {p.symbol || p.mint.slice(0, 8)}
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
          </div>
        )}
      </div>

      <div className="card">
        <h2>Live AI Activity</h2>
        <AiActivityTable rows={activity ?? []} />
      </div>
    </>
  );
}

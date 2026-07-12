import { useState } from "react";
import type { Trade, TradesPage } from "../api";
import { PnlText, RouteBadge, ago, usePoll } from "../components/bits";
import { TradeModal } from "../components/modal";

const PAGE = 25;

export default function Trades() {
  const [offset, setOffset] = useState(0);
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState<Trade | null>(null);
  const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (reason) q.set("reason", reason);
  const { data, error } = usePoll<TradesPage>(`/api/trades?${q}`, 10000);

  if (error) return <p className="neg">Failed to load: {error}</p>;

  return (
    <div className="card">
      <h2>
        Trade history {data ? `(${data.total})` : ""}{" "}
        <span className="hint">click a row for details</span>
      </h2>
      <div className="filters">
        <select
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">all exit reasons</option>
          {["take_profit", "partial_tp", "ratchet_stop", "stop_loss", "max_hold", "trailing_stop", "dead_route", "emergency"].map(
            (r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ),
          )}
        </select>
      </div>
      {!data ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Token</th>
                  <th>Route</th>
                  <th className="num">Size</th>
                  <th className="num">Exit</th>
                  <th className="num">PnL (SOL)</th>
                  <th>Reason</th>
                  <th className="num">Conf</th>
                  <th className="num">Held</th>
                  <th>Closed</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.map((t, i) => (
                  <tr
                    key={`${t.mint}-${t.closed_at}-${i}`}
                    className="clickable"
                    onClick={() => setSelected(t)}
                  >
                    <td className="mono token-cell" title={t.mint}>
                      {t.symbol || t.mint.slice(0, 8)}
                    </td>
                    <td>
                      <RouteBadge route={t.route} />
                    </td>
                    <td className="num mono">{t.size_sol.toFixed(4)}</td>
                    <td className="num mono">{t.exit_sol.toFixed(4)}</td>
                    <td className="num">
                      <PnlText v={t.pnl_sol} />
                    </td>
                    <td className="secondary">{t.reason}</td>
                    <td className="num mono">{t.confidence.toFixed(2)}</td>
                    <td className="num muted">{Math.round(t.held_minutes)}m</td>
                    <td className="muted">{ago(t.closed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              ← previous
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}
            </span>
            <button
              disabled={offset + PAGE >= data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              next →
            </button>
          </div>
        </>
      )}
      {selected && <TradeModal trade={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

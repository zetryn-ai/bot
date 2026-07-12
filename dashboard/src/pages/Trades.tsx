import { useState } from "react";
import type { TradesPage } from "../api";
import { PnlText, ago, usePoll } from "../components/bits";

const PAGE = 25;

export default function Trades() {
  const [offset, setOffset] = useState(0);
  const [reason, setReason] = useState("");
  const q = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
  if (reason) q.set("reason", reason);
  const { data, error } = usePoll<TradesPage>(`/api/trades?${q}`, 10000);

  if (error) return <p className="neg">Gagal memuat: {error}</p>;

  return (
    <div className="card">
      <h2>Riwayat Trade {data ? `(${data.total})` : ""}</h2>
      <div className="filters">
        <select
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">semua reason</option>
          {["take_profit", "stop_loss", "max_hold", "trailing_stop", "dead_route", "emergency"].map(
            (r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ),
          )}
        </select>
      </div>
      {!data ? (
        <p className="muted">Memuat…</p>
      ) : (
        <>
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Token</th>
                  <th className="num">Size</th>
                  <th className="num">Exit</th>
                  <th className="num">PnL (SOL)</th>
                  <th>Reason</th>
                  <th className="num">Conf</th>
                  <th className="num">Held</th>
                  <th>Ditutup</th>
                </tr>
              </thead>
              <tbody>
                {data.trades.map((t, i) => (
                  <tr key={`${t.mint}-${t.closed_at}-${i}`}>
                    <td className="mono" title={t.mint}>
                      {t.symbol || t.mint.slice(0, 8)}
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
              ← sebelumnya
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, data.total)} dari {data.total}
            </span>
            <button
              disabled={offset + PAGE >= data.total}
              onClick={() => setOffset(offset + PAGE)}
            >
              berikutnya →
            </button>
          </div>
        </>
      )}
    </div>
  );
}

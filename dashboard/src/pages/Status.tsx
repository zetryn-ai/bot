import type { Status as StatusData } from "../api";
import { ago, usePoll } from "../components/bits";

export default function Status() {
  const { data, error } = usePoll<StatusData>("/api/status", 10000);
  if (error) return <p className="neg">Gagal memuat: {error}</p>;
  if (!data) return <p className="muted">Memuat…</p>;
  return (
    <div className="card">
      <h2>Status</h2>
      <div className="tablewrap">
        <table>
          <tbody>
            <tr>
              <td className="muted">Database</td>
              <td>{data.db_ok ? "✓ terhubung" : "✕ tidak terhubung"}</td>
            </tr>
            <tr>
              <td className="muted">Aktivitas AI terakhir</td>
              <td>{ago(data.latest_ai_activity)}</td>
            </tr>
            <tr>
              <td className="muted">Trade terakhir</td>
              <td>{ago(data.latest_trade)}</td>
            </tr>
            <tr>
              <td className="muted">Versi bot</td>
              <td className="mono">{data.bot_version}</td>
            </tr>
            <tr>
              <td className="muted">Mode eksekusi</td>
              <td className="mono">{data.execution_mode}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

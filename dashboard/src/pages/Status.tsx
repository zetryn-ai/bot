import type { Status as StatusData } from "../api";
import { ago, usePoll } from "../components/bits";

export default function Status() {
  const { data, error } = usePoll<StatusData>("/api/status", 10000);
  if (error) return <p className="neg">Failed to load: {error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="card">
      <h2>Status</h2>
      <div className="tablewrap">
        <table>
          <tbody>
            <tr>
              <td className="muted">Database</td>
              <td>{data.db_ok ? "✓ connected" : "✕ unreachable"}</td>
            </tr>
            <tr>
              <td className="muted">Latest AI activity</td>
              <td>{ago(data.latest_ai_activity)}</td>
            </tr>
            <tr>
              <td className="muted">Latest trade</td>
              <td>{ago(data.latest_trade)}</td>
            </tr>
            <tr>
              <td className="muted">Bot version</td>
              <td className="mono">{data.bot_version}</td>
            </tr>
            <tr>
              <td className="muted">Execution mode</td>
              <td className="mono">{data.execution_mode}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

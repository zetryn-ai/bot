import type { EquityPoint, Stats } from "../api";
import { BarList, EquityChart } from "../components/charts";
import { usePoll } from "../components/bits";

export default function Analytics() {
  const { data: equity, error } = usePoll<EquityPoint[]>("/api/equity", 10000);
  const { data: stats } = usePoll<Stats>("/api/stats", 10000);

  if (error) return <p className="neg">Gagal memuat: {error}</p>;

  return (
    <>
      <div className="card">
        <h2>Equity Curve (SOL, kumulatif)</h2>
        <EquityChart points={equity ?? []} />
      </div>
      <div className="card">
        <h2>Win-rate per Exit Reason</h2>
        <BarList rows={stats?.by_reason ?? []} labelKey="reason" />
      </div>
      <div className="card">
        <h2>Win-rate per Confidence Band</h2>
        <BarList rows={stats?.by_confidence ?? []} labelKey="band" />
      </div>
      <div className="card">
        <h2>Per Hari</h2>
        <BarList
          rows={(stats?.by_day ?? [])
            .slice(-14)
            .map((r) => ({ ...r, day: String(r.day).slice(0, 10) }))}
          labelKey="day"
        />
      </div>
    </>
  );
}

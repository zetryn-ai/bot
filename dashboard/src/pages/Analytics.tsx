import type { EquityPoint, Stats } from "../api";
import { BarList, EquityChart } from "../components/charts";
import { usePoll } from "../components/bits";

export default function Analytics() {
  const { data: equity, error } = usePoll<EquityPoint[]>("/api/equity", 10000);
  const { data: stats } = usePoll<Stats>("/api/stats", 10000);

  if (error) return <p className="neg">Failed to load: {error}</p>;

  return (
    <>
      <div className="card">
        <h2>Equity curve (SOL, cumulative)</h2>
        <EquityChart points={equity ?? []} />
      </div>
      <div className="card">
        <h2>Win rate by strategy</h2>
        <BarList rows={stats?.by_route ?? []} labelKey="route" />
      </div>
      <div className="card">
        <h2>Win rate by exit reason</h2>
        <BarList rows={stats?.by_reason ?? []} labelKey="reason" />
      </div>
      <div className="card">
        <h2>Win rate by confidence band</h2>
        <BarList rows={stats?.by_confidence ?? []} labelKey="band" />
      </div>
      <div className="card">
        <h2>Daily</h2>
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

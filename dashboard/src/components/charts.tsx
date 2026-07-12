// Hand-rolled SVG charts following the dataviz mark specs: 2px single-series
// line with crosshair+tooltip (no legend — the card title names the series),
// and a labeled bar list for win-rate breakdowns (thin marks, 4px rounded
// data ends, values as text tokens beside the bar — never on every point).

import { useRef, useState } from "react";
import type { EquityPoint, StatGroup } from "../api";

export function EquityChart({ points }: { points: EquityPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const W = 720;
  const H = 220;
  const PAD = { l: 54, r: 12, t: 12, b: 24 };

  if (points.length < 2) {
    return <p className="muted">Belum cukup trade untuk equity curve.</p>;
  }

  const ys = points.map((p) => p.equity_sol);
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(0, ...ys);
  const span = yMax - yMin || 1;
  const x = (i: number) => PAD.l + (i / (points.length - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (1 - (v - yMin) / span) * (H - PAD.t - PAD.b);

  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.equity_sol).toFixed(1)}`).join(" ");
  const zeroY = y(0);

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const hovered = hover !== null ? points[hover] : null;

  return (
    <div className="chart" ref={wrap}>
      <svg viewBox={`0 0 ${W} ${H}`} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {/* recessive grid: zero line + min/max ticks */}
        <line x1={PAD.l} x2={W - PAD.r} y1={zeroY} y2={zeroY} stroke="#32322f" strokeWidth={1} />
        {[yMax, yMin].map((v, k) => (
          <text key={k} x={PAD.l - 6} y={y(v) + 4} textAnchor="end" fontSize={11} fill="#8a897f">
            {v.toFixed(3)}
          </text>
        ))}
        <text x={PAD.l - 6} y={zeroY + 4} textAnchor="end" fontSize={11} fill="#8a897f">0</text>
        <path d={path} fill="none" stroke="#3987e5" strokeWidth={2} />
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b} stroke="#c3c2b7" strokeWidth={1} strokeDasharray="3,3" />
            {/* 8px marker with 2px surface ring */}
            <circle cx={x(hover)} cy={y(points[hover].equity_sol)} r={4} fill="#3987e5" stroke="#1a1a19" strokeWidth={2} />
          </g>
        )}
      </svg>
      {hovered && hover !== null && wrap.current && (
        <div
          className="tooltip mono"
          style={{ left: `${(x(hover) / W) * 100}%`, top: `${(y(hovered.equity_sol) / H) * 100}%` }}
        >
          {new Date(hovered.ts).toLocaleString()} · {hovered.equity_sol >= 0 ? "+" : ""}
          {hovered.equity_sol.toFixed(4)} SOL
        </div>
      )}
    </div>
  );
}

export function BarList({ rows, labelKey }: { rows: StatGroup[]; labelKey: string }) {
  if (!rows.length) return <p className="muted">Belum ada data.</p>;
  return (
    <div className="barlist">
      {rows.map((r, i) => {
        const label = String(r[labelKey]).slice(0, 20);
        const pct = Math.round(r.win_rate * 100);
        return (
          <div className="row" key={i} title={`${label}: ${r.wins}/${r.trades} menang · ${r.pnl_sol.toFixed(4)} SOL`}>
            <span className="secondary mono">{label}</span>
            <div className="track">
              <div className={`fill${r.pnl_sol < 0 ? " negpnl" : ""}`} style={{ width: `${Math.max(pct, 2)}%` }} />
            </div>
            <span className="meta">
              {pct}% · {r.pnl_sol >= 0 ? "+" : ""}
              {r.pnl_sol.toFixed(3)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

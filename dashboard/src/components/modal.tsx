// Row-detail modals — Overview/Trades rows open these on click (no buttons).
// Every field the API returns is rendered; nothing is summarized away.

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { AiActivityRow, OpenPosition, Trade } from "../api";
import { PnlText, RouteBadge, ago, fmtDuration, fmtWhen, outcomeInfo } from "./bits";

export function Modal({
  title,
  onClose,
  children,
}: {
  title: React.ReactNode;
  onClose: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // Portal to <body>: the cards use backdrop-filter, which makes them the
  // containing block for position:fixed descendants — rendered in place, the
  // overlay would be clipped inside the card instead of covering the screen.
  return createPortal(
    <div className="modal-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true">
        <div className="modal-head">
          <div className="title">{title}</div>
          <button className="close" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

function KV({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <div className="kv">
      {rows.map(([k, v]) => (
        <div style={{ display: "contents" }} key={k}>
          <span className="k">{k}</span>
          <span className="v">{v}</span>
        </div>
      ))}
    </div>
  );
}

function MintSection({ mint }: { mint: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="modal-section">
      <h3>Token address</h3>
      <div className="mint-line">
        <code>{mint}</code>
        <button
          className="copy-btn"
          onClick={() => {
            navigator.clipboard?.writeText(mint);
            setCopied(true);
            setTimeout(() => setCopied(false), 1200);
          }}
        >
          {copied ? "copied ✓" : "copy"}
        </button>
      </div>
      <div className="ext-links" style={{ marginTop: 8 }}>
        <a href={`https://solscan.io/token/${mint}`} target="_blank" rel="noreferrer">
          Solscan ↗
        </a>
        <a href={`https://dexscreener.com/solana/${mint}`} target="_blank" rel="noreferrer">
          DexScreener ↗
        </a>
        <a href={`https://gmgn.ai/sol/token/${mint}`} target="_blank" rel="noreferrer">
          GMGN ↗
        </a>
        <a href={`https://birdeye.so/token/${mint}?chain=solana`} target="_blank" rel="noreferrer">
          Birdeye ↗
        </a>
      </div>
    </div>
  );
}

function ScoreMeters({ scores }: { scores: Record<string, number> }) {
  const order = ["safety", "market", "wallets", "social", "final"];
  const keys = [...order.filter((k) => k in scores), ...Object.keys(scores).filter((k) => !order.includes(k))];
  if (!keys.length) return <p className="muted">No per-dimension scores recorded.</p>;
  return (
    <div>
      {keys.map((k) => (
        <div className="meter-row" key={k}>
          <span className="secondary">{k}</span>
          <div className="track">
            <div className="fill" style={{ width: `${Math.round(Math.min(1, Math.max(0, scores[k])) * 100)}%` }} />
          </div>
          <span className="val mono">{scores[k].toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}

// ── AI decision ──────────────────────────────────────────────────────────────

export function AiDecisionModal({ row, onClose }: { row: AiActivityRow; onClose: () => void }) {
  const oc = outcomeInfo(row.outcome);
  return (
    <Modal
      onClose={onClose}
      title={
        <>
          <span className="mono">{row.symbol || row.mint.slice(0, 8)}</span>
          <RouteBadge route={row.route} />
          <span className={`badge ${oc.cls}`}>
            {oc.icon} {oc.label}
          </span>
        </>
      }
    >
      <div className="modal-section">
        <h3>Decision</h3>
        <KV
          rows={[
            ["Time", fmtWhen(row.ts)],
            ["Signal source", row.source || "—"],
            ["Route", row.route || "—"],
            ["AI action", <span className="mono">{row.action}</span>],
            ["Confidence", <span className="mono">{row.confidence.toFixed(2)}</span>],
            ["Final score (raw)", <span className="mono">{row.final_score.toFixed(2)}</span>],
          ]}
        />
      </div>

      <div className="modal-section">
        <h3>Where it stopped</h3>
        <div className={`callout ${oc.cls || ""}`}>
          <strong>
            {oc.icon} {oc.label}
          </strong>
          {oc.explain && <> — {oc.explain}</>}
          {row.outcome_detail && (
            <div style={{ marginTop: 6 }}>
              Detail: <span className="mono">{row.outcome_detail}</span>
            </div>
          )}
        </div>
      </div>

      <div className="modal-section">
        <h3>Scores by dimension</h3>
        <ScoreMeters scores={row.scores} />
      </div>

      {row.reasoning && (
        <div className="modal-section">
          <h3>AI reasoning (full)</h3>
          <div className="reasoning">{row.reasoning}</div>
        </div>
      )}

      {row.reasons?.length > 0 && (
        <div className="modal-section">
          <h3>Decision reasons / guardrails</h3>
          <ul className="reason-list">
            {row.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}

      <MintSection mint={row.mint} />
    </Modal>
  );
}

// ── open position ────────────────────────────────────────────────────────────

export function PositionModal({ pos, onClose }: { pos: OpenPosition; onClose: () => void }) {
  const heldS = (Date.now() - new Date(pos.opened_at).getTime()) / 1000;
  return (
    <Modal
      onClose={onClose}
      title={
        <>
          <span className="mono">{pos.symbol || pos.mint.slice(0, 8)}</span>
          <RouteBadge route={pos.route} />
          <span className="badge info">◉ {pos.execution_mode}</span>
        </>
      }
    >
      <div className="modal-section">
        <h3>Position</h3>
        <KV
          rows={[
            [
              "Unrealized PnL",
              pos.unrealized_pnl_pct === null ? (
                <span className="muted">not marked yet</span>
              ) : (
                <span
                  className={`mono ${pos.unrealized_pnl_pct > 0 ? "pos" : pos.unrealized_pnl_pct < 0 ? "neg" : "muted"}`}
                >
                  {pos.unrealized_pnl_pct >= 0 ? "+" : ""}
                  {(pos.unrealized_pnl_pct * 100).toFixed(1)}%{" "}
                  <span className="muted">(marked {ago(pos.marked_at)})</span>
                </span>
              ),
            ],
            ["Opened", fmtWhen(pos.opened_at)],
            ["Held for", `${fmtDuration(heldS)}`],
            ["Entry size", <span className="mono">{pos.size_sol.toFixed(4)} SOL</span>],
            ["Tokens held (atomic)", <span className="mono">{pos.tokens_atomic.toLocaleString()}</span>],
            ["Entry confidence", <span className="mono">{pos.confidence.toFixed(2)}</span>],
            ["Route", pos.route || "— (pre-routing position)"],
            ["Status", pos.status],
            ["Execution mode", pos.execution_mode],
          ]}
        />
      </div>

      <div className="modal-section">
        <h3>Exit plan (snapshot at entry)</h3>
        <KV
          rows={[
            ["Take profit", <span className="mono pos">+{(pos.take_profit_pct * 100).toFixed(0)}%</span>],
            ["Stop loss", <span className="mono neg">−{(Math.abs(pos.stop_loss_pct) * 100).toFixed(0)}%</span>],
            ["Max hold", fmtDuration(pos.max_hold_s)],
            [
              "Time left before max hold",
              heldS >= pos.max_hold_s ? (
                <span className="mono neg">expired — exit pending sweep</span>
              ) : (
                fmtDuration(pos.max_hold_s - heldS)
              ),
            ],
          ]}
        />
      </div>

      {pos.partials.length > 0 && (
        <div className="modal-section">
          <h3>Partial exits (TP ladder)</h3>
          <ul className="reason-list">
            {pos.partials.map((pe, i) => (
              <li key={i}>
                Sold <span className="mono">{pe.sold_size.toFixed(4)} SOL</span> of basis at the{" "}
                <span className="mono pos">+{(pe.sold_at_pnl_pct * 100).toFixed(0)}%</span> rung —{" "}
                {ago(new Date(pe.sold_at_ts * 1000).toISOString())}
              </li>
            ))}
          </ul>
        </div>
      )}

      <MintSection mint={pos.mint} />
    </Modal>
  );
}

// ── closed trade ─────────────────────────────────────────────────────────────

export function TradeModal({ trade, onClose }: { trade: Trade; onClose: () => void }) {
  const pnlPct = trade.size_sol ? (trade.pnl_sol / trade.size_sol) * 100 : 0;
  return (
    <Modal
      onClose={onClose}
      title={
        <>
          <span className="mono">{trade.symbol || trade.mint.slice(0, 8)}</span>
          <RouteBadge route={trade.route} />
          <span className={`badge ${trade.pnl_sol > 0 ? "good" : trade.pnl_sol < 0 ? "bad" : ""}`}>
            {trade.pnl_sol > 0 ? "▲ profit" : trade.pnl_sol < 0 ? "▼ loss" : "— flat"}
          </span>
        </>
      }
    >
      <div className="modal-section">
        <h3>Result</h3>
        <KV
          rows={[
            [
              "Realized PnL",
              <span>
                <PnlText v={trade.pnl_sol} /> SOL{" "}
                <span className={pnlPct > 0 ? "pos" : pnlPct < 0 ? "neg" : "muted"}>
                  ({pnlPct >= 0 ? "+" : ""}
                  {pnlPct.toFixed(1)}%)
                </span>
              </span>,
            ],
            ["Entry size", <span className="mono">{trade.size_sol.toFixed(4)} SOL</span>],
            ["Exit value", <span className="mono">{trade.exit_sol.toFixed(4)} SOL</span>],
            ["Tokens (atomic)", <span className="mono">{trade.tokens_atomic.toLocaleString()}</span>],
            ["Exit reason", <span className="mono">{trade.reason}</span>],
          ]}
        />
      </div>

      <div className="modal-section">
        <h3>Timeline</h3>
        <KV
          rows={[
            ["Opened", fmtWhen(trade.opened_at)],
            ["Closed", fmtWhen(trade.closed_at)],
            ["Held for", fmtDuration(trade.held_minutes * 60)],
          ]}
        />
      </div>

      <div className="modal-section">
        <h3>Entry context</h3>
        <KV
          rows={[
            ["Route", trade.route || "— (pre-routing trade)"],
            ["Entry confidence", <span className="mono">{trade.confidence.toFixed(2)}</span>],
            ["Execution mode", trade.execution_mode],
          ]}
        />
      </div>

      <MintSection mint={trade.mint} />
    </Modal>
  );
}

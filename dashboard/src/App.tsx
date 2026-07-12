import { useState } from "react";
import { clearToken, getToken } from "./api";
import Analytics from "./pages/Analytics";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Status from "./pages/Status";
import Trades from "./pages/Trades";

const TABS = ["Overview", "Trades", "Analytics", "Status"] as const;
type Tab = (typeof TABS)[number];

export default function App() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const [tab, setTab] = useState<Tab>("Overview");

  if (!authed) return <Login onOk={() => setAuthed(true)} />;

  return (
    <>
      <header>
        <h1 className="brand">
          ZETRYN <span className="brand-sub">trading agent</span>
        </h1>
        <nav>
          {TABS.map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t}
            </button>
          ))}
        </nav>
        <button
          className="logout"
          onClick={() => {
            clearToken();
            setAuthed(false);
          }}
        >
          Sign out
        </button>
      </header>
      {tab === "Overview" && <Overview />}
      {tab === "Trades" && <Trades />}
      {tab === "Analytics" && <Analytics />}
      {tab === "Status" && <Status />}
    </>
  );
}

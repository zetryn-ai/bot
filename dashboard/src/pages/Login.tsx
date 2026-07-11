import { useState } from "react";
import { api, setToken } from "../api";

export default function Login({ onOk }: { onOk: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setToken(value.trim());
    try {
      await api("/api/auth/check");
      onOk();
    } catch {
      setError("Token salah.");
    }
  };

  return (
    <form className="login" onSubmit={submit}>
      <h1>zetryn-bot</h1>
      <p className="secondary">Masukkan dashboard token.</p>
      <input
        type="password"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="DASHBOARD_TOKEN"
        autoFocus
      />
      {error && <p className="error">{error}</p>}
      <button type="submit">Masuk</button>
    </form>
  );
}

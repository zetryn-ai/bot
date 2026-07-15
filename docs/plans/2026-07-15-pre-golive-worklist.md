# Pre-Go-Live Worklist — dikerjakan sebelum Sabtu 18 Juli 2026

**Date:** 2026-07-15
**Status:** Approved
**Pengerjaan:** Claude Fable (branch dev; JANGAN sentuh `main` produksi kecuali hot-fix)
**Konteks live saat ini:** bot **v0.13.0** + framework **v1.4.0** (git-pin `@v1.4.0`,
belum di PyPI) berjalan paper di VPS `/opt/zetryn-bot` via `docker-compose.vps.yml`.
Dashboard LIVE https://zetryn.lemacore.com.

> Dokumen ini adalah hasil sesi analisa forensik + Sniper v2 (2026-07-15) plus
> jawaban 4 pertanyaan strategis user. Semua angka di bawah **berbasis data DB
> dry-run nyata**, bukan asumsi. Baca §7 (fakta & jebakan) sebelum mulai.

---

## 0. TL;DR keputusan

- **Mesin profit yang berulang = route `graduation`** (+0.365 SOL / 2 hari,
  62.5% WR, 24 trade). Sniper +0.565 SOL **99% dari SATU token "bulk" (19× ×3)**
  = lotere, bukan edge stabil.
- **Target 0.5 SOL/hari dari 1 SOL (50%/hari) TIDAK didukung data.** Hari terbaik
  nyata = +0.32 SOL, dan strategi **tidak scale linear** (likuiditas target
  $1.5k–40k → posisi besar = slippage membunuh edge).
- **Go-live penuh Sabtu BERISIKO** karena 3 blocker P0 di bawah. Rekomendasi:
  staged go-live (graduation-only, cap 0.02–0.05 SOL, smoke-test 1–2 tx nyata dulu).
- **Fokus pengembangan (urut): Eksekusi → Data/filter → Exit tuning → (jauh) AI.**
  Route paling profit jalan **rule-mode tanpa LLM**, jadi menaikkan kualitas
  prompt = mengoptimalkan bagian yang lemah.
- **⭐ QUICK WIN (P1-5):** band keputusan `watch` (conf ~0.54) **rugi −0.39 SOL**
  (113 trade, 40% WR, tanpa moonshot); `buy` (~0.89) **+0.95 SOL** & memuat semua
  moonshot; `alert` (~0.77) +0.04 tipis. Buang `watch` dari `RISK_BUY_ACTIONS`
  → +0.49 SOL historis jadi **~+0.88 SOL**, nol risiko. Scoring/tiering framework
  **sudah SESUAI** (band naik → WR naik); yang salah cuma konfig bot menradingkan
  tier terendah.

---

## 1. P0 — BLOCKER go-live (wajib selesai sebelum Sabtu)

### P0-1. Deferred-retry buy untuk route graduation  🔴 blocker #1
- **Masalah (data+kode):** setelah fix fantom v0.12.1, curve fallback dimatikan
  untuk non-sniper. Buy graduation sekarang **gagal `no quote` sampai Jupiter
  mengindeks pool** (beberapa menit pasca-migrasi). Route TERBAIK jadi tidak
  menembak di menit-menit paling menguntungkan.
- **Target:** saat buy graduation gagal karena belum ter-index, jangan buang
  sinyal — antre + retry quote Jupiter dengan backoff (mis. tiap 10–15 dtk,
  batas ~5 menit / max-attempts), lalu fill saat quote pertama valid muncul.
  Jangan pakai curve fallback (itulah yang bikin fantom).
- **Sentuh:** `zetryn_bot/execution/executor.py` (jalur buy), `execution/position.py`,
  mungkin sink/queue di `pipeline/`. Simpan pending-buy state agar survive tick.
- **Acceptance:** test unit "buy graduation gagal quote → masuk pending → fill saat
  quote muncul, TIDAK fill via curve"; log live menunjukkan graduation fill nyata
  pasca-migrasi tanpa harga fantom (cek entry price BUKAN 2.8e-14).

### P0-2. Smoke-test jalur transaksi REAL (belum pernah dilakukan)
- **Masalah (memory M5):** `LiveExecutor` hanya pernah diuji lawan RPC/Jupiter
  **mock**. Belum ada 1 transaksi on-chain nyata yang pernah dikirim.
- **Target:** eksekusi terkontrol 1–2 buy+sell nyata nominal sangat kecil
  (≤0.02 SOL) di wallet live, verifikasi: sign lokal (solders), submit
  `lite-api.jup.ag/swap/v1/swap`, konfirmasi on-chain, `check_signature_landed`
  saat timeout (anti double-swap), PnL tercatat benar di DB.
- **Sentuh:** `execution/live.py`, `execution/rpc.py`; `SOLANA_RPC_URL` di VPS
  `.env` **masih UUID telanjang** — harus URL RPC penuh (mis.
  `https://mainnet.helius-rpc.com/?api-key=<key>`) sebelum bisa submit.
- **Acceptance:** signature nyata di Solscan, DB `closed_trades` konsisten dengan
  on-chain, tidak ada double-swap saat konfirmasi lambat.

### P0-3. Slippage & priority-fee guard untuk live
- **Masalah:** paper fill tidak memodelkan slippage; likuiditas target tipis
  ($1.5k–40k). Tanpa guard, buy real bisa slip 30%+ dan menghapus edge.
- **Target:** slippage-bps diturunkan dari tier likuiditas; batas max-slippage
  yang membatalkan buy; `prioritizationFeeLamports` wajar (bukan "auto" buta).
- **Sentuh:** `execution/live.py`, `execution/risk.py`, config `.env`.
- **Acceptance:** test derivasi slippage per tier; buy dibatalkan bila slippage
  quote > batas.

---

## 2. P1 — Sangat disarankan sebelum Sabtu (menutup jurang paper→real)

### P1-1. Model slippage + fee di PaperExecutor
- Supaya angka paper bisa dipercaya sebagai proxy real. Tanpa ini, +0.5 SOL
  paper overstated. Kurangi PnL paper dengan estimasi slippage (dari likuiditas)
  + fee tetap. **Sentuh:** `execution/executor.py`.

### P1-2. Persist peak PnL (trailing stop)
- **Masalah (memory M10a):** peak PnL in-memory → restart me-re-arm trailing dari
  0 → posisi yang sudah +50% bisa mengembalikan gain. **Target:** simpan peak ke
  kolom `positions`, restore saat reconcile. **Sentuh:** `execution/lifecycle.py`,
  `execution/position.py`, `db/models.py` + migration.

### P1-3. Entry snapshot → aktifkan emergency (rug) gate
- **Masalah:** tracker tak simpan enrichment saat entry → gate `emergency_exit`
  framework **inert**. Snapshot sudah disimpan ke `ai_decisions.snapshot`; teruskan
  ke `PositionContext.token` agar rug-exit hidup. **Sentuh:** `execution/lifecycle.py`,
  `execution/position.py`.

### P1-4. Validasi Sniper v2 & exit baru dengan data
- v0.13.0 baru live beberapa jam. Kumpulkan sampel: apakah `stagnation_stop`
  (flat 300s → exit) dan tier scoring (`score N -> tier`) berperilaku benar di
  feed. Bandingkan WR sniper v2 vs v1. **Bukan kode — analisa data.**

### P1-5. Buang band `watch` dari `RISK_BUY_ACTIONS`  ⭐ quick win ROI tertinggi
- **Masalah (data DB, join closed_trades→ai_decisions per action band):**

  | Band | conf | n | PnL SOL | WR | avg/trade | win terbaik |
  |---|---|---|---|---|---|---|
  | **buy** | ~0.89 | 36 | **+0.950** | 63.9% | +0.0264 | +0.294 (moonshot ada di sini) |
  | **alert** | ~0.77 | 16 | +0.038 | 62.5% | +0.0024 | +0.023 |
  | **watch** | ~0.54 | 113 | **−0.393** | 39.8% | −0.0035 | +0.043 (tanpa ekor moonshot) |

  Band `watch` **menghancurkan modal**: −0.39 SOL dari 113 trade, 45 menang /
  68 kalah, dan **tidak ada moonshot** yang tersembunyi (win terbaik cuma
  +0.043). Semua profit + semua moonshot ada di band `buy`. Framework MEMANG
  menandai tier ini "watch, jangan beli" — konfig bot `RISK_BUY_ACTIONS=
  alert,watch,buy` yang memaksa membelinya. **Data membenarkan framework.**
- **Target:** ubah `RISK_BUY_ACTIONS=alert,buy` (buang `watch`). Estimasi
  dampak historis: total PnL naik dari +0.49 → **~+0.88 SOL** (menghilangkan
  −0.39 tanpa kehilangan satu pun moonshot). Reversible, nol risiko kode.
- **Catatan:** `alert` positif tapi tipis (n=16, +0.038) — pertahankan untuk
  sekarang, pantau; kalau tetap marginal di window berikutnya, pertimbangkan
  `RISK_BUY_ACTIONS=buy` saja.
- **Sentuh:** VPS `.env` (`RISK_BUY_ACTIONS`), `.env.example`. **Bukan kode.**
- **Kaitan skor:** tiering framework SUDAH SESUAI arah — makin tinggi band,
  makin tinggi WR & profit (watch 40% → alert/buy ~63%). Yang salah bukan
  scoring-nya, tapi keputusan bot untuk menradingkan tier terendah.

---

## 3. P2 — Setelah go-live aman / paralel di branch dev

- **M11 live-hardening penuh:** Jito bundle, PumpPortal trade API untuk curve
  live (bukan hanya paper curve math), retry submit yang lebih pintar.
- **Wire calibration layer:** framework punya `CalibrationMap` + `Backtester`/
  replay (v1.2.0) — skor mentah → win-rate empiris per source; backtest window
  dry-run ini SEBELUM menaikkan sizing real.
- **Observability:** equity curve per-route di dashboard + alert drawdown harian.
- **Tuning exit `launch`:** hold rata-rata ~2 menit, keluar breakeven (−0.039);
  eksperimen max-hold / ladder.
- **Review gate `momentum`:** 0 trade closed (gate anti-laggard terlalu ketat,
  ~54 skip/5mnt). Longgarkan bertahap dengan data 6×24 di checkpoint.

---

## 4. Track paralel yang user minta (branch terpisah, ortogonal — tidak blokir go-live)

> Ini pengembangan produk, aman dikerjakan berbarengan di branch lain.

- **P-A. De-hardcode config:** audit semua nilai hardcoded + yang seharusnya di
  `.env`; pindahkan ke `Settings` (pydantic). Buat inventaris dulu (grep angka/URL
  ajaib di `zetryn_bot/`), lalu migrasi bertahap dengan test.
- **P-B. Multi-account / multi-user product:** desain isolasi per-user (wallet,
  posisi, risk, DB tenancy, dashboard token per-user). **Butuh design doc
  tersendiri** (`docs/plans/`) — jangan langsung koding; ini perubahan arsitektur
  besar dan menyentuh boundary framework/bot. Brainstorm dulu.
- **P-C. Fitur terparkir** (audit "ready-tapi-tidak-jalan"): inventarisasi apa saja
  yang sudah ada di kode tapi belum di-wire/di-enable, prioritaskan.

---

## 5. Bug/temuan yang SUDAH diperbaiki sesi ini (jangan diulang, untuk konteks)

- ✅ **Fantom graduasi +13.07 SOL** — curve fallback fill di harga awal curve
  pasca-migrasi. Fix v0.12.1: fallback sniper-only; 20 row dihapus; risk_state
  dihitung ulang → PnL jujur +0.52 SOL. (Metode forensik: fingerprint entry price
  = size_sol/tokens_atomic; palsu = 2.8e-14 identik.)
- ✅ **Jotchua +282,015%** — junk-quote guard (konfirmasi 2-sweep ≥20×; fill >3×
  dari sweep dibuang) v0.11.3.
- ✅ **Bug guard enricher pumpfun_meta** — cek `"pumpfun_ws"` padahal nama scanner
  `"pumpfun.ws"` → enricher tak pernah jalan di produksi. Fix v0.13.0 (prefix match).

---

## 6. Appendix data — dry-run (DB `zetryn_bot`, per 2026-07-15)

**Per hari (realized):**

| Hari | Trades | PnL SOL | Avg size | WR |
|---|---|---|---|---|
| 07-10 | 1 | −0.0215 | 0.060 | 0% |
| 07-11 | 65 | −0.1356 | 0.064 | 29.2% |
| 07-12 | 47 | +0.0346 | 0.057 | 44.7% |
| 07-13 | 71 | +0.2915 | 0.046 | 43.7% |
| 07-14 | 29 | +0.3236 | 0.040 | 58.6% |
| 07-15 | 16 | +0.0189 | 0.034 | 62.5% |

**Magnitude:** loser 131 trade avg −0.0135 SOL (−23.4%); winner 98 trade avg
+0.0233 SOL (+110.9% — tapi p50 hanya +34.5%, ditarik outlier).

**Per-route (07-13 & 07-14, dua hari penuh):**

| Route | n | PnL | WR | Avg size | Catatan |
|---|---|---|---|---|---|
| sniper | 4 | +0.5647 | 75% | 0.015 | **3 trade = token "bulk" 19× → lotere** |
| graduation | 24 | +0.3651 | 62.5% | 0.049 | **mesin nyata, repeatable** |
| launch | 12 | −0.0386 | 50% | 0.018 | breakeven, perlu tuning |
| scanner (legacy) | 60 | −0.2761 | 40% | 0.050 | loss sink; sudah dibubarkan M12 |

**5 winner terbesar:** bulk/sniper +0.294 (19.6×), bulk/sniper +0.144 (19.1×),
bulk/sniper +0.136 (18.2×), graduation +0.133 (3.0×), graduation +0.099 (4.4×).

**Profitabilitas per ACTION BAND** (join closed_trades→ai_decisions, ts terdekat
opened_at; lihat P1-5): buy 36 trade **+0.950** SOL 63.9% WR · alert 16 trade
+0.038 SOL 62.5% WR · watch 113 trade **−0.393** SOL 39.8% WR (45W/68L, win
terbaik hanya +0.043 → tanpa ekor moonshot) · rule-mode/legacy tak-ter-match 70
trade −0.104 SOL. **Kesimpulan: watch = loss sink murni; buy = mesin profit +
semua moonshot; scoring/tiering framework directionally SESUAI (band naik → WR
naik).**

**Distribusi keputusan (ai_decisions, sepanjang dry-run):** skip 10,995
(conf 0.05) · abort 5,531 (0.0) · watch 3,787 (conf 0.54) · buy 178 (conf 0.89)
· alert 52 (conf 0.77). Hanya 52 alert & 178 buy seumur hidup → sinyal band
tinggi langka; volume trade selama ini didominasi watch (yang justru rugi).

---

## 7. Fakta & jebakan operasional untuk executor (Fable) — BACA

- **Boundary NON-NEGOTIABLE:** bot = I/O/eksekusi/persistensi; framework
  (`zetryn-trading`) = keputusan/scoring/LLM. Jangan taruh logika keputusan di
  `zetryn_bot/`. Perubahan scoring/exit-rule = repo framework + rilis.
- **Conda env:** bot = `zetryn-bot`; framework = `zetryn`. Framework v1.4.0 sudah
  `pip install -e` ke env `zetryn-bot`.
- **Test bot:** `cd /mnt/data/Project/zetryn/bot && python -m pytest tests/ -q`
  (baseline 160 pass, 22 skip). SELALU `cd` eksplisit — CWD suka reset. Jangan
  `| tail` yang menutup exit code.
- **Deploy VPS:** compose default `docker-compose.yml` HANYA punya service
  `postgres`. Bot+dashboard ada di **`docker-compose.vps.yml`** (service `bot`,
  `dashboard`). Build+up:
  `docker compose -f docker-compose.vps.yml build && ... up -d`.
- **DB:** container `postgres-16` (bukan `docker-db-1`), db `zetryn_bot`, user
  `zetryn`. Numpang network odoo-lema.
- **Dashboard API:** Bearer token; `DASHBOARD_TOKEN` di VPS `.env`; uvicorn
  127.0.0.1:8140.
- **Commit:** patch → `./scripts/commit-as.sh random "msg" main`; minor/major →
  identitas `zetryn` inline + annotated tag. Trailer `Co-Authored-By: Claude
  Fable 5`.
- **API eksternal:** verifikasi spec vendor terkini SEBELUM koding (Jupiter/
  pump.fun/GMGN drift; memory training usang). `frontend-api-v3.pump.fun` (un-
  versioned = 530); Jupiter quote/swap = `lite-api.jup.ag/...` (v6 lama MATI).
- **Notifier Telegram:** plain text, TANPA parse_mode — jangan pakai tag HTML.
- **Framework belum di PyPI:** bot pin `git+...@v1.4.0`. Setelah user beri token
  PyPI & framework v1.4.0 dipublish, kembalikan pin ke `zetryn-trading>=1.4.0`.
- **Keamanan:** passphrase wallet BARU ditaruh di VPS saat benar-benar live;
  secrets tak pernah masuk git/image; `.env` chmod 600.

---

## 8. Urutan kerja yang disarankan (menuju Sabtu)

0. **P1-5 buang `watch` dari `RISK_BUY_ACTIONS`** — quick win, lakukan DULU
   (edit `.env`, restart; nol risiko, +0.39 SOL historis).
1. P0-1 deferred-retry graduation (blocker #1).
2. P0-2 fix `SOLANA_RPC_URL` + smoke-test 1–2 tx real ≤0.02 SOL.
3. P0-3 slippage/priority-fee guard.
4. P1-1 slippage model paper (validasi angka), P1-2 persist peak, P1-3 rug gate.
5. P1-4 kumpulkan data Sniper v2 untuk laporan checkpoint.
6. Checkpoint Sabtu 18 Juli 21:00 WIB: keputusan go-live (staged, graduation-only,
   cap kecil) + user serahkan data KOL wallet → mulai M10c.

Track paralel (§4) jalan di branch terpisah kapan saja; P-B multi-user WAJIB
lewat design doc + brainstorm dulu.

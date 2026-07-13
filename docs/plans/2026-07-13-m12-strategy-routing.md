# M12 — Strategy-first routing (membubarkan route "scanner")

**Date:** 2026-07-13
**Status:** Shipped (v0.12.0)
**Target version:** v0.12.0 (menggantikan slot; M10c KOL bergeser ke v0.13.0
bersama feed webhook — KOL menjadi route ke-6 di tabel ini)

## Masalah (temuan user 2026-07-13)

Route **"scanner" bukan strategi** — ia adalah agen generalis framework
(`build_scanner`): hard-gates → analis LLM 4-dimensi → skip/watch/alert.
Routing M10b hanya punya 3 aturan; SEMUA yang bukan pump.fun jatuh ke
scanner: token momentum (gecko/dexscreener/birdeye trending), pool baru
(gecko_new, dexscreener new_pairs, raydium, birdeye new_listing), dan call
telegram — satu agen, satu set gate, satu profil exit untuk pola sinyal
yang berbeda total. Akibat terukur: 83% diet LLM = trending laggard, entry
di puncak lokal, stop-loss 0-6 menit.

## Desain: 6 route = 6 pola sinyal

| Route | Primary sources | Karakter sinyal | Gate masuk khas | Exit khas |
|---|---|---|---|---|
| ⚡ sniper | pumpfun_ws (age<=120s) | launch detik-detik | rule murni (curve SOL, rug) | ladder + ratchet (tetap) |
| 🎓 graduation | pumpfun_migration | lulus curve → DEX | fill-speed, top10, safety | ladder + ratchet (tetap) |
| 🚀 launch | geckoterminal_new, dexscreener (new_pairs), raydium, birdeye_new | pool umur < LAUNCH_MAX_AGE (default 2h) | umur muda; holders rendah DIBOLEHKAN; wajib rugcheck+safety; liq floor rendah | size kecil; max_hold pendek (default 45m); ladder lebar |
| 📈 momentum | geckoterminal_trending, dexscreener_trending, birdeye_trending | sudah bergerak — beli KELANJUTAN, bukan puncak | **anti-laggard**: Δ5m > 0, Δ1h ∈ (0, MOM_MAX_1H], Δ6h <= MOM_MAX_6H (default +150%), buyers_5m naik, buy-ratio > 0.5 | TP pendek (default ladder 0.2:0.5,0.4:1.0); max_hold default 60m; trailing ketat |
| 📣 social | telegram_* (+ nanti sinyal twitter velocity) | call/mention | wajib konfirmasi on-chain: liq+vol floor, safety; umur < SOCIAL_MAX_AGE | profil launch |
| 👑 kol | kol_buy (M10c webhook) | smart-money entry | qualifikasi wallet | profil momentum |

Fallback tetap ada (route `other`, observasi-only / conf floor tinggi) —
tidak ada lagi tong sampah yang ikut trading.

## Mekanika (bot-side, framework tidak berubah)

- Agen LLM **tetap `build_scanner`** untuk launch/momentum/social — yang
  berbeda per route: `ScannerConfig` (gate), **route-context line di
  TokenInput source**, dan kebijakan Risk/exit. Boundary aman: bot memilih
  konfigurasi, framework tetap memutuskan.
- `RiskConfig` per-route DIPERLUAS: selain `route_size_multipliers` +
  `route_conf_floors` (ada), tambah `route_exit_overrides`
  (`ROUTE_EXIT_OVERRIDES="momentum:tp_ladder=0.2:0.5,0.4:1.0;max_hold=3600|launch:max_hold=2700;size=0.5"`
  — format final di implementasi) yang mengisi SwapRequest TP/SL/max-hold +
  ladder per posisi. LifecycleEngine sudah membaca ladder per posisi?
  (saat ini global — perlu per-position ladder: simpan ladder di Position
  atau map route→engine; keputusan implementasi: SATU LifecycleEngine per
  route, dipilih tracker berdasarkan position.route.)
- Anti-laggard momentum gate dihitung dari field v0.11.1 (Δ, buyers unik,
  buy-ratio) — pre-filter bot SEBELUM LLM (hemat budget + fokus).
- Semua threshold via .env; default di atas.

## Keputusan terkunci (user, 2026-07-13)

1. Pecah SEKALIGUS 5+1 route. ✅
2. Gate momentum default (Δ5m>0, 0<Δ1h≤80%, Δ6h≤150%, buyers>sellers, buy-ratio>0.5). ✅
3. Exit per route sesuai tabel (momentum 0.2/0.4 + mh 60m; launch 0.5× + mh 45m). ✅
4. dexscreener_boost: tetap blocked, jatuh ke `other` observasi-only. ✅

## DoD

- Distribusi ai_decisions per route mencerminkan pola sinyal (trending →
  momentum, pool baru → launch); tidak ada trade beroute `scanner` lagi.
- Momentum route menolak kandidat Δ6h > threshold (terlihat sebagai
  rule_skip reasons).
- Win-rate per route terukur di Analytics (sudah ada by_route).
- Suite hijau; `ROUTING_ENABLED=false` tetap bit-for-bit lama.

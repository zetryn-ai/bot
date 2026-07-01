"""Runtime layer — the long-running orchestration process (M3).

Ties the M1 scanners and M2 pipeline together into a runnable process:
`build_enabled_scanners` / `build_enrichers` wire config to instances,
`Orchestrator` runs them concurrently through a shared queue + worker pool,
and `zetryn_bot.__main__` is the entry point (`python -m zetryn_bot`).
"""

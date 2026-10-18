# Backlog

Ideas and decisions that are not part of the current phase. Nothing here is committed to; each item says when it becomes relevant. The pre-registered documents (Phase 0, the Phase 1 plan) are not changed by anything on this page; where an item implies a change to them, it says so and the change goes through a new timestamped version.

## 1. Public page on the BRLabs site (Phase 5, after the wallet is live)

What it shows:

- The current gross value of both wallets, the budget wallet (X) and the savings wallet, in dollars. Decided 2026-10-17: absolute values are published. The test capital is small enough that nobody mounts an operation to steal it and large enough to count as skin in the game.
- A quota curve, base 100, which is the performance measure: time-weighted, so contributions and sweeps to savings do not show as gains or losses. The budget wallet is pinned at X by design and its balance alone says nothing about the algorithm. Benchmarks B0 to B4 go on the same chart.
- Current allocation in percent, the monthly return table (fund fact-sheet style), and the history of sweeps to savings, which is the "extra income" story told in numbers.
- The timeline of timestamped commitments (Phase 0 hash, Phase 1 plan hash, pre-registered hypotheses), each with its OpenTimestamps proof.

How it works:

- Every number comes from the timestamped ledger, never from a live query to the exchange. The bot publishes a static JSON snapshot each cycle; the page reads and draws it. The site never holds an API key.
- Allocation is published with a delay (after the execution window, or one day) and a plain "this is my own portfolio, not a recommendation" notice. Cheap precaution; see item 7.
- Before Phase 5 the page can already exist, showing the walk-forward results labeled as a backtest and the commitment timeline.

Implied change to Phase 0: the security rule "never publish balances" becomes "never publish the savings wallet address or any key; balances may be published". Goes into the next timestamped version of the Phase 0 document.

## 2. Publishing sequence

- Pre-registration note: a short post (BRLabs site or LinkedIn) pointing at the hashes already on GitHub and on the Bitcoin chain. It exists to carry a date; no audience is expected or sought.
- Launch post, when the wallet goes live: real money, public wallet, what it is doing, and the link to what was committed before, with its date.
- Monthly results series, which is what keeps readers: the return table, what the bot did, including bad months. If a kill criterion fires, the post says "this was one of the outcomes written down in October".

## 3. Other markets: perpetual swaps (not before the spot track record exists)

- The archive publishes USDT-M futures klines under `data/futures/um/` in the same format as spot, plus funding-rate history. The data pipeline (list, fetch, verify, convert, quality) extends with a prefix change.
- What does not carry over is the risk model: leverage, liquidation and funding are a different game. The signal layer and the treasury assume long/cash with non-negative positions.
- Open question tied to the October 2026 migration: whether Binance Brasil offers derivatives to residents at all.

## 4. "Buy me a coffee" QR code (any time)

- Goes in the README and on the site. BTC on-chain fits the thesis, Pix serves Brazilians, GitHub Sponsors is the open-source default. All three can coexist.
- Rule: a dedicated donation wallet, never the savings wallet (publishing that address is exactly what Phase 0 forbids), and never mixed with X. Donations are income with their own tax treatment and would pollute the capital-gains ledger.

## 5. Paid tier with support (only after six to twelve months of live track record)

- Open-core: the algorithm and the laboratory stay open under Apache 2.0, including for anyone who wants to do the same with this code. What is paid is convenience and support: treasury with sweeps, the Brazilian tax ledger with GCAP export, assisted and signal execution modes, a hosted results page.
- Each person installs and runs it on their own machine with their own keys, at their own risk. That is the only shape in which this is selling software rather than managing money. The line never crossed, at any tier: never hold anyone else's keys or funds.
- Why anyone would pay given that Freqtrade exists and is free: a specific algorithm with a verifiable, timestamped record, plus the treasury and tax layers a framework does not have.
- Terms, disclaimers and liability wording need a lawyer before the first sale.

## 6. Name and slogan

The project is the anti-degen by construction (volatility targeting, cash as a position, kill criteria). The joke belongs in the tagline rather than the name: "the least degen bot in crypto". A paid tier could be called "degen" for the same reason.

## 7. Legal check (Phase 5, low priority)

Publishing one's own live portfolio versus selling signals. As far as we know, crypto assets that are not securities fall outside the CVM's remit and the 2022 framework law put the sector under the Central Bank, which regulates service providers, not people showing their own holdings. Confirm with a lawyer before the public page goes live; the one-day delay in item 1 is the precaution in the meantime.

## 8. Crowding: what happens if many installations run the same signal (Phase 3 and V2)

The signal is identical for every installation on purpose; that is what makes the public record verifiable. The portfolio is not: X, venue, jurisdiction, execution mode and the volatility target shape it. Two consequences to design for, in order:

- Execution (Phase 3): the decision is the same for everyone, so the orders must not be. Tranches of 1/6 every 4h already spread the flow across the day; add a per-installation random offset inside the execution window. The backtester's capacity figure (aggregate capital above which costs eat the edge, from the 1% ADV participation cap and the impact model) is published as a number.
- Signal diversity (V2, a Phase 2 research question first): a family of pre-registered variants (half-lives of 15, 20 and 30 days, slightly different bands), one per installation by seed. Different installations enter and exit on different days, each variant has its own public record, and ensembles of lookbacks are usually as good as or better than any single one. Risk appetite (the volatility target, chosen among 15, 25 and 35%) scales positions but does not de-synchronise flow, and a "target return" is not a parameter a strategy can honour: risk is chosen, return is the outcome.

Scale check: a thousand installations of 1k to 10k dollars are 1 to 10 million dollars against pairs that trade 50 million to tens of billions a day. Trend following is the most crowded strategy class in existence and works at the scale of the asset class because it is slow; crowding hurts fast signals. If crowding ever degrades the signal, the public record degrades with it and the kill criteria fire: the system has its own thermostat, and being open makes the thermostat visible.

## 9. Notes from the first full pipeline run (2026-10-17), for Phase 1 piece 8 and later

- 755 pairs, 2017 to 2026, 1d/4h/1h: 1.2 GB of zips, 26.1 million candles, 8885 Parquet partitions. Fetch 2h19 at 16 workers (latency-bound, about 10 files per second), verify offline in 36 s, convert in 5 min, quality in 49 s.
- The archive is clean: 2 OHLC errors in 26 million rows, every jump confirmed by the hourly data. Dead pairs are files that stop, not zero-volume candles (7 zero-volume runs in total), so `symbols.last_candle` is the death detector.
- Findings cluster by date, not by pair: the same dates appear across many pairs (2021-01-21 and 2021-12-23 to 26 in the cross-interval check; February and June 2018 in the gaps). On daily data this does not touch eligibility (the daily candle is always published; the outages show in 1h and 4h), so the daily universe does not apply gap findings at all. The execution layer (Phase 3), which runs on 4h and 1h, must treat a gap shared across most pairs on the same date as an exchange event, which penalizes nobody, and a lone gap as the pair's own problem.
- The universe excludes by asset type (`lastro/universe/assets.py`): leveraged tokens (UP, DOWN, BULL, BEAR suffixes on a listed asset; they are derivatives with built-in decay, and most of the 2353 jumps are theirs), stablecoins and fiat pairs (no trend to follow), wrapped or pegged assets. Edit the lists there.
- Symbol reuse (LUNAUSDT after May 2022) is handled by the history clock: a gap longer than 7 days restarts it, so the pair is a new listing with 60 days to wait. `symbols.alias_of` stays unused until a confirmed rename needs it.
- The 22 tokens restricted in Brazil from 2026-10-27 are in `lastro/universe/restrictions_br.csv` as reported by the press coverage of the Binance Brasil announcement of 2026-10-08. Every report prints "HIT", which is not a Binance ticker; HFT (Hashflow, delisted globally in August 2026 with ACX, PIVX, PYR, VANRY and VIC) is the probable reading and is included too. Confirm against the official FAQ, which refuses automated access.
- `data convert` rewrites every partition on each run (idempotent, not incremental, about 5 minutes for the full set). Fine for now; make it incremental if runs become frequent.
- The 382 files verified before piece 6 have no `.CHECKSUM` companion on disk. The freeze manifest (piece 8) carries every file's digest, so reproduction does not depend on the companions.
- Parquet (1.48 GB) is larger than the zips (1.19 GB) because float64 columns compress worse than decimal text. Irrelevant now; `use_byte_stream_split` would shrink it if it ever matters.

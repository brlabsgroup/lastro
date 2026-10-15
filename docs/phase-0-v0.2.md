# Lastro, Phase 0: Rules of the Game

Version 0.2, October 2026. Caue Bertolino, BRLabs.

Changelog. v0.1 (Portuguese, timestamped on 15 October 2026 01:56 UTC via OpenTimestamps, SHA-256 97cc10adae2d584d6cac57929ff647159bd562c50eae3055bbb3e9d401ff8a50): first registration. v0.2: English translation; internal inconsistencies left by incremental edits resolved; resolved questions removed from section 15. Sections 9 (hypotheses) and 10 (kill criteria) are unchanged in substance from v0.1.

## The thesis, in prose

Crypto has a property you can measure with public data: what went up over the last few weeks tends to keep going up for a while, and what went down tends to keep going down. Not always, but more often than chance, and for a reason that does not go away: people arrive late to rallies and leave late from declines, and large money moves slowly. The phenomenon has a name, trend and momentum, is documented in decades of finance literature, and keeps existing because whoever captures it has to accept long sideways stretches and give back part of every wave. Lastro rides that phenomenon: it buys what has been rising, sells to dollars what has been falling, and never tries to call a top or a bottom. It misses the start and the end of every wave by design. That is the price of not having to predict.

The second pillar is that crypto volatility is far more predictable than crypto returns. If a coin swung hard this week, it will probably swing hard next week; nobody knows the direction, but the agitation persists. That allows giving each coin a slice inversely proportional to its agitation, so that no single coin dominates the outcome, and scaling the whole account to a level of fluctuation chosen in advance. When the market goes wild, part of the capital moves to cash; when it calms down, it comes back. The account's risk stops being whatever the market decides that month and becomes what the operator decided beforehand. It is this sizing, not the choice of coins, that separates compounding for years from blowing up in one cycle.

The third pillar is that retail has a real niche: the 20 to 50 most liquid coins below the very top are too small for funds to enter without moving the price and too slow to interest anyone competing on latency. A retail account rebalancing once a day moves nothing. Lastro does not compete with market makers or funds; it operates where they are not.

What the system delivers is not a secret strategy: trend and momentum are public. What it delivers is verifiable discipline. A universe that includes the coins that died, so the test does not interview only survivors. Execution on the next candle, net of costs, so the backtest does not pretend prices that never existed. Hypotheses and kill criteria written and timestamped before any result, so that nobody, the author included, can move the goalposts after seeing the score. And a treasury that defines profit as what left for a self-custodied wallet outside the exchange, not as the number on the screen. The bet is that this, done with public data and open code, is worth more than any promise of returns: whoever runs Lastro knows exactly what they are running, what it costs, and what would have happened had they done otherwise.

The expected result is modest and explicit: 20 to 40% a year, with drawdowns of 25 to 40% at some point, most of the gain concentrated in a few months of each cycle, and whole years going sideways. If the research shows that not even this holds up net of costs, the result gets published all the same. The laboratory is the product; the strategy is whatever survives it.

## 1. What the system is

Lastro is a long/cash spot allocator: once per candle, it decides how much of the trading capital X sits in each of the 20 to 50 most liquid coins on the exchange and how much sits in stablecoin, and sends only the orders for the difference.

The goal is to capture most of crypto's long rallies and skip most of its declines, with the maximum loss decided beforehand. It does not predict: it reacts to what is already happening and controls risk. In a typical year: a few months of large gains, many months sideways, some months of losses.

What it is not:

- A new-coin hunter (launches, bonding curves, pump.fun).
- An intraday bot, a market maker or an arbitrageur.
- Leveraged or short: it only buys, or sits in cash.

The parts:

| Part | What it does | Rule that governs it |
| --- | --- | --- |
| Strategy | Pure function: data up to t produces target weights | The same function runs in the backtest and in production |
| Executor | Reads real balances, computes the difference to the target, and either sends the orders (automatic mode) or hands the order list to the human to execute and confirm (assisted mode) | Stateless: reconciles every round; talks to the exchange only through the connector |
| Ledger | Records every fill in USDT and carries the operator's fiscal-currency rate (BRL by default) at that instant; average cost in fiscal currency is derived, never the primary record | Matches the exchange statement to the cent |
| Backtester | Simulates the strategy on the past | Execution at t+1, modeled costs, built-in benchmarks |
| Treasury | Manages X, sweeps and savings | A layer above the strategy, switchable in the backtest |

The strategy receives candles and returns target weights; the executor reconciles with the exchange and feeds the ledger; the backtester consumes the same data and the same function; the treasury sets X above everything and sweeps the surplus out of the exchange.

Product: open source under Apache 2.0, an individual running it on their own server. No custody, no signals for sale.

## 2. Fixed decisions

These decisions are not reopened before the end of Phase 3. Any change requires a new version of this document, with a written justification.

| Decision | Choice | Reason |
| --- | --- | --- |
| Capital | Own capital, individual, Brazilian tax residency | Defines ledger, tax treatment and size |
| Instrument | Spot only (buying and selling the coin itself, no derivatives), in USDT-quoted pairs: everything is bought and sold against USDT, and cash is USDT | No liquidation, no funding, no shorting |
| Exchange | Platform-agnostic code: strategy, backtester, treasury and ledger know nothing about any exchange; only the connector (CCXT) and the cost model are venue-specific. Initial venue: Binance. Three execution modes, configurable: automatic (orders via API), assisted (the bot produces the order list, the human executes and confirms) and signal (target weights only) | Research and execution on the same order book, public historical archive, 0.10% fee. Binance Brazil's October 2026 migration left API access for residents uncertain: the product cannot depend on one exchange. Switching venue recalibrates costs, filters and universe, not the strategy |
| Horizon | Swing on daily candles; 4h and 1h as testable variants, with position tranching as an execution variant (section 4) | Competes on research, not on latency |
| Out of scope | Intraday (deferred until real costs are measured), market making, arbitrage, new coins | Latency and PvP games where retail loses by structure |
| Distribution | Open source under Apache 2.0, self-hosted, no custody, no signal selling | Removes nearly all of the regulatory question; Apache adds a patent clause and a stronger no-warranty notice |
| Sizing | Volatility targeting; total exposure never above 100% | Harvests the market's volatility, controls the account's |
| Real profit | What was swept to the savings wallet | Treasury, section 11 |
| Scale | Software agnostic to X: money in Decimal, exchange filters respected, universe width derived from X, capacity as a backtester output | Serves from a few hundred dollars up to wherever impact allows |
| Data | Parquet and DuckDB for market data; SQLite for ledger, configuration and state; public sources | Reproducible by anyone, zero cost, no server |
| Pre-registered expectation | Net Sharpe above 1 at a 25 to 30% volatility target: 20 to 40% a year with 25 to 40% drawdowns | The ceiling test (section 8) says whether this is conservative or optimistic |

## 3. Universe and width

The universe is defined by rule and by date, never by a fixed list. On each decision date, eligibility uses only data available up to that date.

Eligibility:

- USDT-quoted spot pair on the venue, with active trading status on that date.
- At least 90 daily candles of history.
- 30-day median volume above US$ 5 million per day.
- Excluded: stablecoins and fiat- or gold-pegged tokens (USDC, FDUSD, TUSD, DAI, PAXG), leveraged tokens (UP, DOWN, BULL, BEAR), wrapped or derived versions of another asset in the universe (WBTC, WBETH, stETH).
- BNB is in the universe. If the fee discount is enabled, the BNB reserve for fees sits outside the weights.
- Jurisdiction restrictions: a dated restriction list per jurisdiction (the 22 tokens restricted for Brazilian residents from 27 October 2026, for instance) removes pairs from the configured jurisdiction's universe without touching the global one.
- Ranking: median dollar volume over the last 30 days. The top 50 eligible pairs form the maximum universe.

Width N (how many coins the panel uses) is derived from X, not fixed:

- Minimum useful position = 5 times the pair's minimum notional, read from the exchange filters (5 to 10 USDT on most pairs today). With 10 USDT, the minimum position is 50 USDT: a 25% adjustment of it still clears the minimum.
- N = floor(X / minimum position), bounded between N\_min and 50. N\_min is configurable, default 5; the value 1 is allowed and H2 with K equal to 1 tests that mode, but below 5 coins the report warns that there is no diversification and that leader-switching turnover weighs more. Examples with a 10 USDT minimum: X of 250 gives 5 coins; 1,000 gives 20; 2,500 or more gives 50.
- Floor: below N\_min minimum positions (250 USDT with a notional of 10 and N\_min of 5), the bot refuses to run and explains why.
- A position that falls far does not get stuck: the floor exit (section 5) sells everything before the value drops below the exchange minimum.

Delisting:

- Delisting announcement: sell to cash at the next rebalance, or immediately if the exit date comes first.
- In the backtest, the asset leaves the universe on the announcement date. The position is liquidated at the next day's close with a 2% haircut.
- Suspension without notice: position marked at the last price and flagged as illiquid. The backtester reports PnL with and without it.

## 4. Horizon and execution

The decision is made at the close of the daily candle (00:00 UTC) and executed on the next candle. Never at the closing price of the decision itself: that is the classic backtest bug, and here it is forbidden by design. There are three clocks: decision at every candle, execution inside the window, risk continuously (section 12).

- Decision: close of t, with data up to t.
- Execution: a random window within the first 60 minutes after the open of t+1. The offset is drawn per installation, per day, so that N copies of the bot do not send the same orders in the same minute. Inside the window, the minute is chosen by the taker-buy flow of the 30- and 5-minute candles: flow in the order's favor accelerating, execute; decelerating, wait, up to the window limit.
- Orders: limit at the best price in the book, repositioned every 30 seconds. After 10 minutes without a full fill, the remainder goes to market.
- Order slicing (TWAP): an order above 1% of the asset's daily volume is spread across the window. Whatever does not fit the participation cap (section 6) carries over to the next day.
- Backtest: fill at the open of t+1 plus the modeled cost of section 6.
- 4h and 1h variants: same logic, decision at the close of the 4h or 1h candle. Each enters as a separate hypothesis, with the higher turnover cost counted.
- Position tranching variant: the signal is evaluated every 4h, but only 1/6 of the position change is executed per evaluation. The first slice goes out 4 hours after the signal flips, the full position forms in one day, no decision depends on a single closing time, and a false crossover costs 1/6 of a round trip. The backtester compares the three: daily, 4h and tranched 4h.
- Stale data: if the latest available candle is more than 2 periods old, the bot does not trade and alerts.

## 5. Sizing

Each coin with its signal on receives a weight inversely proportional to its volatility, and the whole account is scaled to a volatility target. Total exposure never exceeds 100%: when the target asks for less than that, the rest sits in cash.

- Per-asset volatility estimate: EWMA of daily log returns, 20-day half-life (testable range: 10 to 40).
- Raw weights: among assets with the signal on, proportional to 1 / vol, normalized to sum to 1. Similar risk contribution per asset; correlation enters only through the portfolio volatility.
- Account volatility target: 25% a year (default; 30% testable; configurable by the operator). Portfolio volatility is estimated with the EWMA covariance of the raw weights.
- Scaling factor capped at 1: never leverages. A factor below 1 sends the difference to cash.
- Per-asset cap: the larger of 25% of X and 1/N, so that with few coins the cap does not block exposure (testable range for the floor: 20 to 35%). The excess cut by the cap goes to cash and is not redistributed.
- No-trade band: an asset is rebalanced only if the gap between target weight and current weight exceeds 25% of the target weight, with a floor of 0.5 percentage points of X, or if the signal changed state. An order below the minimum notional is accumulated to the next day.
- Floor exit: if a position's value falls below 1.5 times the minimum notional, the bot sells all of it at the next rebalance, regardless of the signal, so that no position turns to dust. The backtester simulates this rule with the configured X.
- Cash: USDT. Yield on the cash leg is a Phase 5 topic.

As a formula, with s\_i equal to 1 (on) or 0 (off), sigma\_i the asset's volatility, sigma\* the volatility target and sigma\_p the volatility of the raw-weight portfolio r:

```latex
r_i = \frac{s_i / \sigma_i}{\sum_j s_j / \sigma_j}, \qquad w_i = \min\left(w_{\max},\; \lambda\, r_i\right), \qquad \lambda = \min\left(1,\; \frac{\sigma^{*}}{\hat{\sigma}_p(r)}\right)
```

The cash fraction is 1 minus the sum of the w\_i.

## 6. Cost model

Every simulation is net of fees, spread and impact, with a per-asset participation cap. The stress variant doubles everything, and a strategy that does not survive it dies (section 10).

| Component | Default | Note |
| --- | --- | --- |
| Fee | 0.10% per side (taker) | 0.075% with BNB only if the BNB reserve is modeled; the default assumes taker out of conservatism |
| Half spread | 0.03% for the top 10 of the ranking; 0.08% from 11th to 50th | Estimated from data when a book is available; otherwise the default |
| Impact | asset's daily vol times sqrt(Q / ADV), coefficient 1.0 | Square-root model. Q = order value in dollars; ADV = 30-day average daily volume |
| Participation cap | 1% of ADV per asset per day | Excess deferred to the next day; the backtester records the delay and its cost |
| Stress | 2 times fee, spread and impact | Mandatory run for every hypothesis |
| Sweep | Network withdrawal fee, per network | Minimum lot such that the fee stays below 5% of the swept amount |
| Capacity | The X at which net CAGR falls 20% relative to small X | Mandatory backtester output, per hypothesis |

The model has three layers of source, per venue. Live: fees, filters and the order book are read from the API at order time; each order's expected cost is computed against the real book and recorded before sending. Historical: the backtest uses the fee schedule in force on each date and spread and impact estimated from the data itself, because historical order books are not freely available. Calibration: the real cost of every fill (decision price versus executed price) is recorded in the ledger and, after 60 days of data, replaces the venue defaults. If real costs exceed 2 times the modeled ones, the model is wrong and the research returns to Phase 2 with the new numbers.

## 7. How it is measured

The yardstick is B2: BTC with a trend filter, net of costs. A strategy that does not beat B2 on both Sharpe and Calmar is just an expensive way to hold the same beta.

| Code | Benchmark | Role |
| --- | --- | --- |
| B0 | Cash in USDT, zero return | Floor |
| B1 | BTC buy and hold | Pure beta |
| B2 | 100% BTC when the close is above the 200-day average; cash when below; net of costs | What has to be beaten |
| B3 | Equal-weight basket of the point-in-time top N, rebalanced monthly | What the universe delivered |
| B4 | BTC accumulation: monthly contribution, reinforced buying when the price is below the 200-day average, never sells | What a disciplined investor does without a bot |

Mandatory metrics, all net of costs, over the out-of-sample period, reported in aggregate, per year and per regime (BTC above or below its 200-day average). With contributions enabled, time-weighted (TWR) and money-weighted (IRR) returns, with the same contributions applied to every benchmark:

- Return: CAGR, annualized volatility, net Sharpe, Sharpe deflated by the number of trials in the log (section 8), Calmar (CAGR over maximum drawdown).
- Risk: maximum drawdown and its duration, worst month, PnL concentration (largest year over the total).
- Operation: annual turnover in multiples of X, total annual cost as % of X, fraction of time in cash, average exposure, orders per month.
- Robustness: Sharpe with each parameter shifted 30% in each direction; Sharpe under the 2x cost stress; capacity (section 6).
- Tax: total sold per month and realized gain per month, in the configured fiscal currency (BRL by default; USD when none is configured).
- Operating cost: a fixed monthly cost declared by the operator (server, domain, whatever applies) deducted from net PnL; the report shows profit before and after it and the minimum X at which the operation pays for itself.
- Treasury: equity curve with and without sweeps, and the cost of the compounding forgone.

## 8. Research protocol

Out-of-sample data is touched once per hypothesis. Everything that was tried is logged and discounts the Sharpe.

- Sample: Binance spot data since August 2017.
- Development: August 2017 to December 2022. Covers the 2018 bear, the March 2020 crash, the 2021 bull and the 2022 bear.
- Holdout: January 2023 up to the dataset freeze date. Locked until the end of Phase 3.
- Walk-forward inside development: expanding training window with a 2-year minimum, 1-year test, 1-year step. Three test windows: 2020, 2021 and 2022. Parameters chosen on training data only.
- Holdout: one run per surviving hypothesis, with the parameters fixed in development. Fails, dies. No adjustment after seeing the holdout.
- Trial log: every backtester run records the configuration hash, timestamp, window, metrics and dataset hash, in an append-only file. The number of trials per hypothesis feeds the deflated Sharpe.
- Parameter discipline: at most 3 free parameters per hypothesis, a small grid defined in advance (section 9), chosen at the center of the plateau and never at the peak.
- Ceiling test, before the hypotheses: B2, B3 and a simple unoptimized trend rule run on the point-in-time universe, net of costs, and the result is published. The ceiling is not a hypothesis: it is the yardstick that calibrates the expectation of section 2 before any thesis is tested.
- Frozen dataset: the set used in Phase 3 has a published hash. An update produces a new hash and reopens the log; it never overwrites.

## 9. Pre-registered hypotheses

Four hypotheses, in this order. H1 and H2 are independent; H3 and H4 are layers on top of them. All use the sizing of section 5 and the costs of section 6. No other thesis enters Phase 3 without a new version of this document.

| Hypothesis | Signal | Free parameters (grid) | Dies if |
| --- | --- | --- | --- |
| H1 Per-asset trend | On when the close is above the L-day average; off when below. Variant: 12-month return, excluding the last month, positive | L in {50, 100, 150, 200} days; decision candle in {1d, 4h, 1h} | Does not beat B2 net in the walk-forward |
| H2 Relative momentum | Rank the universe by J-month return, skipping the last week; the top K are on | J in {1, 3, 6}; K in {1, 5, 10, N/2} | Does not beat B2 and B3 net in the walk-forward |
| H3 BTC regime | Alts on only when BTC is above its M-day average; BTC below sends everything to cash | M in {100, 200}; applied over H1 and over H2 | Does not improve the base Calmar |
| H4 Volume confirmation (secondary) | H1 signal counts only with 30-day volume above V times the 180-day average. Variant on 4h: taker-buy volume imbalance | V in {1.0, 1.2}; applied over H1 | Does not improve H1's net Sharpe |

What each one tests: H1 asks whether each coin's trend pays after costs. H2 asks whether picking the best among them pays more than holding all. H3 asks whether the BTC regime protects the basket. H4 answers the volume question: whether it adds anything to price at this horizon.

## 10. Kill criteria

Written before any result. One failed criterion is enough for the hypothesis to die at that stage, and there is no "fix and rerun" within the same stage.

| Stage | Dies if | Limit |
| --- | --- | --- |
| Backtest (walk-forward, net) | Aggregate net Sharpe below | 0.7 |
| Backtest | Deflated Sharpe not distinguishable from zero | p above 0.05 |
| Backtest | Does not beat B2 on Sharpe and on Calmar | Both |
| Backtest | Maximum drawdown above | 45% at a 25% volatility target |
| Backtest | Concentration: largest year above 70% of PnL, or fewer than half the years positive | Either |
| Backtest | Fragility: shifting one parameter by 30% flips the sign of the Sharpe | Any parameter |
| Backtest | Stress: Sharpe with doubled costs below | 0.4 |
| Backtest | Annual turnover above | 30 times X |
| Holdout | Any criterion above, in a single run | No adjustment |
| Shadow (60 days) | Real cost above 2 times modeled; drift between target and actual above 5 points without an identified cause; more than 2 pipeline failures | Back to Phase 2 or 4 |
| Live | Drawdown above 1.5 times the worst of the backtest | Stop and investigate |
| Live | 6 months of negative realized Sharpe while B2 is positive | Stop |
| Live | Any kill switch from section 12 | Stop until manual review |

A dead hypothesis is published with the same prominence as a live one. The expected outcome of Phase 3 is that most of them die.

## 11. Treasury

The treasury manages the trading capital X and defines real profit as what left for the savings wallet. It is a layer separate from the strategy, simulated in the backtester with an on/off switch, so that the cost of the compounding forgone shows up as a number.

| Parameter | Default | Configurable to |
| --- | --- | --- |
| X, trading capital | Set by the operator | Any value above the floor of section 3. Every change is a ledger event, with a date |
| Contributions | Detected, never automatic: a deposit arriving in the account shows up as a pending contribution and the operator confirms whether it joins X or stays out | In the backtester only, a fixed monthly amount, applied equally to every benchmark |
| Sweep trigger | High-water mark: only what exceeds X | Monthly calendar |
| Sweep timing | When exposure is at 20% or less (bot in cash) | Month end |
| Minimum lot | Network fee below 5% of the amount, never below the withdrawal minimum | Fixed amount |
| Direction | One-way: no top-up | Two-way: tops up to X when below, limited to Z% of savings per year |
| Savings currency | USDT | BTC or another asset in the universe |
| Destination | Address outside the exchange, on the withdrawal allowlist | Must be outside the exchange |
| Raising X | Manual: the operator changes X whenever they want, and each change becomes a ledger event with a counterfactual | By rule, optional: X rises Z% every N months above the high-water mark, for whoever prefers to take their hand off the decision |
| Sweep execution | Bot notifies, human withdraws | Separate withdrawal key, restricted to the allowlisted address |
| Fiscal currency | BRL | Any currency with an available exchange-rate series; sets the currency of tax reports and acquisition cost |

Treasury report:

- Realized profit per savings currency, in its own unit (USDT, BTC), and its current value in the fiscal currency and in USD.
- Total equity marked to market: trading plus savings.
- Counterfactual for every manual change of X: what the system would have made had X not changed. The operator's timing, as a number.
- Equity curve with and without sweeps, to see the price of not compounding.

Swapping USDT for BTC in savings is a swap and enters the ledger as a disposal, with the acquisition cost in the fiscal currency carried until the future sale.

## 12. Operational safety

The worst case is losing X. No failure of code, exchange or operator may lose more than that.

- Exchange cap: X plus a 5% buffer for fees. Savings stay outside, in self-custody.
- API key: trading only, no withdrawals, restricted to a fixed IP. Automated withdrawals, if enabled, use a separate key with allowlisted destinations.
- Kill switches, all with a Telegram alert:
  - Daily loss above 5% of X: stop.
  - Data more than 2 candles old: do not trade.
  - Order above 30% of X: rejected.
  - 3 consecutive API errors: stop.
  - Drift between target and actual portfolio above 5 percentage points: alert and open no new position.
- Risk clock, continuous: a position that falls more than 3 times its daily volatility within the day exits immediately, without waiting for the decision candle to close. Fires only in the tail; the backtester simulates it on 1-hour candles.
- Stateless executor: every round reads real balances and reconciles. A restart does not duplicate orders (idempotency via client order id).
- Clone herd: random window and limit orders, as in section 4.
- Daily report via Telegram (free; other channels via plugin): current state, proposed state, orders executed, final state, plus real versus modeled slippage and exchange balance versus cap. In assisted mode, the order list goes out this way and the confirmation comes back the same way. Venue comparison: in shadow, the same weight panel is priced against the order book of every connected venue, and the report shows the daily execution cost on each; real orders go only to the active venue.
- Infrastructure: a small VPS, a scheduled job, logs and a daily balance snapshot stored off the VPS.
- Resumption: after any stop, it only restarts after manual review, with the reason logged.

## 13. Data and ledger

The data is public, local and reproducible. The ledger exists from the first fill and matches the exchange statement.

Data (Phase 1 requirements):

- Primary source: Binance's public historical archive (data.binance.vision), spot candles at 1 day, 4 hours and 1 hour, plus 30 and 5 minutes for execution timing (section 4) and 1 hour for the risk clock (section 12). The archive keeps delisted pairs (confirmed on 14 October 2026: the BTCBUSD monthly file for June 2023 is still served, last modified after the BUSD delisting), so the list of every pair that ever existed comes from enumerating the archive's folders, and the delisting month is the last file present for each pair.
- Metadata: snapshots of each pair's filters and status (exchangeInfo), listing and delisting dates.
- USDT/BRL series: the USDTBRL pair from the archive itself, cross-checked against the Central Bank's PTAX, for acquisition cost in the fiscal currency.
- Checks before any use: gaps, duplicates, zero-volume candles, UTC timestamps, returns above 50% in one candle confronted with a secondary source.
- Published dataset hash. Parquet partitioned by pair and year for market data (candles, order books), which is written once and read thousands of times; DuckDB for queries. Everything operational (ledger, treasury events, trial log, configuration, executor state, tax rules) lives in a SQLite database: one file, no server, with transactions, which DuckDB reads directly to join with the candles.

Ledger (from day 1):

- Per fill: timestamp, pair, side, quantity, price, fee and fee currency, fiscal-currency rate at that moment, derived average cost and realized gain in the fiscal currency.
- Per month: total sold, realized gain or loss, loss carry-forward balance, in the fiscal currency.
- Swaps (USDT for BTC in the treasury) recorded as disposals.
- Exports: monthly capital gains in the GCAP format and the month's operations in the DeCripto format. Tax rules live in a database table, per country and with validity dates (currency, assessment period, exemption, rates, cost method, whether swaps count as disposals): Brazil ships with the install, another country is a new seed, and a rule that changes on a date gets a new row without deleting the old one.
- Monthly reconciliation against the exchange statement; 5-year retention.
- Treasury events (change of X, sweep, top-up) in the same ledger. Tax regime configurable per period, with a cut-off date: foreign entity (annual assessment, Law 14.754, DeCripto filed by the user) until the migration, Brazilian entity (monthly assessment, GCAP, R$ 35,000 exemption, reporting by the exchange) afterwards; the ledger exports in both formats and does not offset losses across the two periods. Crypto-to-crypto swaps count as disposals toward the monthly threshold. Snapshot of balances and average cost per asset on the eve of the migration.

None of this is accounting advice. Exemption, assessment and loss-offset rules are confirmed with an accountant before Phase 5.

## 14. Publication and timestamping

This document is timestamped before any result exists. After that, hypotheses and criteria do not change: they change version.

- Timestamp: the SHA-256 hash of each version anchored in Bitcoin via OpenTimestamps, published in the repository alongside the proof file. Zero cost, verifiable by anyone.
- Immutability: sections 9 and 10 are frozen by the timestamp. Any change becomes a new version, with a justification, and the backtester reports results against the timestamped version in which the hypothesis was born.
- Public repository from Phase 1: Apache 2.0 license, a notice that nothing in it is investment advice, no guarantee of results.
- Public trial log, with the configuration and dataset hashes.
- In production: the hash of each day's target weights is published the same day; the full decision record is revealed with a 7-day delay.
- Negative results are published with the same prominence as positive ones. "Tested 4 hypotheses, 3 died" is a deliverable, not a failure.
- What is not published: keys, balances, withdrawal addresses, and any PnL figure without the log that backs it.

## 15. Phases and open questions

Phases 1 to 3 risk no money and are where the project lives or dies. Each phase closes only by its exit criterion.

1. Phase 1, data: reproducible pipeline, hashed dataset, delisted coverage confirmed, checks passing. Exits when the dataset reproduces on another machine with the same hash.
2. Phase 2, backtester and ceiling: execution at t+1, costs of section 6, benchmarks of section 7, walk-forward, trial log, deflated Sharpe. Exits when B1 and B2 reproduce a known reference and the ceiling test is published.
3. Phase 3, research: H1 to H4 in the walk-forward; survivors in the holdout, once. Exits with zero or more live hypotheses and the report published, dead ones included.
4. Phase 4, shadow for 60 days: same code, real data, no order sent. Exits with real costs within 2 times the modeled ones and 30 consecutive days without a pipeline failure.
5. Phase 5, small live: initial X, kill switches on, treasury on, yield on the cash leg evaluated. Monthly review against section 10.

Open questions:

- [ ] Accountant: confirm the Brazilian-entity regime (monthly capital gains, R$ 35,000 exemption on the month's total disposals, 15% to 22.5%, DARF, loss offset) and the cut-off: operations before the migration fall under Law 14.754 (annual, 15%, no exemption) and do not offset against later ones.
- [ ] Binance: the migration to the Brazilian entities (Binance Brasil CCVM, formerly Sim;paul, for BRL; BBrasil PSAV for crypto) is automatic for Brazilian residents, deadline 29 October 2026; 8 products restricted and 22 tokens delisted on 27 October; international crypto deposits and withdrawals require purpose and counterparty from 1 November (BCB Resolution 521). Still open: does spot remain on the global USDT order book? Do API keys remain available to residents? Check the official FAQ and, after the migration, test the API on the account itself. If the API goes away, Phase 5 runs in assisted or signal mode, or on another venue; Phases 1 to 3 do not change.

Sources consulted on 13 and 14 October 2026:

- [Binance public archive, binance-vision client](https://pypi.org/project/binance-vision/)
- [Binance spot fees and CNV registration](https://www.datawallet.com/de/krypto/beste-krypto-b%C3%B6rsen-argentinien)
- [Coinbase Advanced fees](https://coincodecap.com/coinbase-fees)
- [DeCripto, IN RFB 2.291/2025](https://guiatributario.net/2025/11/17/)
- [IN 1888 revoked from 1 July 2026](https://www.legisweb.com.br/legislacao/?id=377332)
- [R$ 35,000 exemption and foreign exchanges, 2026 tax return](https://www.adrianofreire.com.br/blog/como-declarar-criptomoedas-ir-2026)
- [BCB Resolutions 519, 520 and 521](https://renovainvest.com.br/?p=73161)
- [Binance Brazil migration, October 2026](https://www.declarandobitcoin.com.br/post/a-binance-vai-migrar-suas-opera%C3%A7%C3%B5es-para-o-brasil-em-outubro-de-2026-entenda-a-isen%C3%A7%C3%A3o-de-r-35-mil)
- [Restricted products and delisted tokens, Binance Brazil](https://bitnoticias.com.br/regulacao/binance-brasil-migracao-contas-deslistagem-tokens-margem/)

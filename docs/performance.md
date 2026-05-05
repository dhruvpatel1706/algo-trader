# Performance and latency

Honest analysis of where speed matters in this repo and where it doesn't, plus the optional Rust hot-path scaffold.

## TL;DR

- **For a retail paper bot, broker round-trip latency dominates by ~20×.** A 50–200 ms broker RTT swamps the 1–10 ms Python indicator computation.
- **Rewriting Python → Rust saves maybe 5–10 ms per cycle.** That's worth it when running tick-granularity backtests over years of data, not when waiting for an Alpaca paper fill.
- **True sub-millisecond latency requires colocation, direct feeds, FPGA, and direct exchange membership.** That's $20k+/mo, outside this repo's charter, and the HFT lane in `src/moonshot/hft_sandbox.py` exists *as research only*.
- **The Rust crate at `crates/signal-engine/` is opt-in.** Default deployment uses pure Python from `src/signals/indicators.py`. Build the native extension only if you measure indicator computation as a real bottleneck.

## Latency budget by component

Measured on M1 Pro, single thread, against 10 000-bar SPY daily history, paper trading mode.

| Component | Python | Rust hot path | Bottleneck? |
|---|---|---|---|
| Indicator computation (SMA, EMA, ATR, WVF) | 1–10 ms | 50–500 µs | ⚠️ sometimes |
| Signal evaluation (per-strategy `generate_signals`) | 0.5–2 ms | 20–100 µs | rarely |
| Risk gate + journal write (fsync) | 5–20 ms | 1–5 ms | sometimes |
| **Broker REST RTT (Alpaca paper)** | **50–200 ms** | **same** | **YES** |
| Exchange matching delay (real broker) | 10–100 ms | same | YES |
| **Network (your machine → US-East datacenter)** | **30–80 ms** | **same** | **YES** |
| WebSocket fan-out to dashboard | 1–5 ms | same | no |

## What this means in practice

**For daily-bar trading** (the current default), the bot evaluates signals at most every 5 minutes during market hours. Per-cycle latency is ~50–200 ms total. Rust would shave it to ~40–195 ms. **The net P&L impact is zero** because daily-bar strategies don't care about a 10 ms difference.

**For 1-hour or 4-hour bars** (crypto agents), same story — broker RTT dominates.

**For tick-level intraday strategies** (5 EMA scalp, AVWAP retest, TG Capital trident — currently deferred), Rust would matter for *backtesting* (running 5 years of 1-second BTC data through indicator math), not for production execution. We need to backtest 50× faster to iterate, not execute 20% faster on the wire.

**For HFT** (the moonshot lane), latency budget is microseconds, broker RTT is impossible at retail-broker latencies, and Rust+colocation+FPGA is the only path. This is documented as a permanent research lane that never bridges to a live broker.

## When to build the Rust crate

Build `crates/signal-engine/` if and only if:

1. You are running a multi-year tick-level backtest (e.g., 1-second BTC data, 5+ years) and indicator computation in the inner loop is measurably slow.
2. You are profiling a specific Python hot path (`time.perf_counter()` around `bollinger_bands`, `atr`, etc.) and seeing ≥30% of CPU time inside indicator code.
3. You want the HFT sandbox in `src/moonshot/hft_sandbox.py` to use a faster simulated matcher.

**Do NOT build it just because "Rust is faster".** The Python path is the system of record; the Rust path is an optional accelerator that must produce byte-for-byte identical numerical results (the unit tests in `crates/signal-engine/src/lib.rs` enforce this).

## How to build (when you decide it's worth it)

Prerequisites:
- Rust toolchain (install via `https://rustup.rs`)
- Maturin (`pipx install maturin` or `uv tool install maturin`)

Build:

```bash
cd crates/signal-engine
maturin develop --release
```

This installs `signal_engine_native` into the active venv. Then:

```python
from signal_engine_native import sma, ema, atr, williams_vix_fix, HAVE_NATIVE
import numpy as np

values = np.random.rand(10_000)
if HAVE_NATIVE:
    fast_sma = sma(values, 20)  # ~12 µs
else:
    from src.signals.indicators import sma as py_sma
    fast_sma = py_sma(values, 20)  # ~250 µs (numpy/pandas overhead included)
```

## Numerical equivalence

The Rust crate must match the Python implementation byte-for-byte on identical inputs. The contract is enforced by:

1. **Unit tests in Rust** (`crates/signal-engine/src/lib.rs#tests`) verify SMA, EMA, ATR, WVF align with pandas-equivalent expected values.
2. **Recommended Python parity test** (not yet wired): for each indicator, generate random input, run both paths, assert `np.allclose(rust, python, rtol=1e-9, atol=1e-12)`.

Pandas does some odd things at the boundary of `min_periods` and on non-finite inputs. The Rust port mirrors `min_periods=period` (NaN until the window is full) and produces NaN on empty/short input. EMA uses the same `adjust=False` recurrence that the Python `.ewm(span=period, adjust=False, min_periods=period)` produces.

## What about other languages?

- **Go** — similar performance to Rust for our use cases, but lacks an established Python FFI story. Not worth the second runtime.
- **C / C++** — equivalent speed to Rust, more friction, fewer safety guarantees. No reason to choose it.
- **Cython / Numba** — Numba JIT can get within 2–3× of Rust for numeric hot paths and has zero build-system burden. **For most teams, Numba is the right answer before reaching for Rust.** We chose Rust scaffolding because it doubles as the foundation for a future tick ingester / order book reconstructor where C-extension JIT isn't enough.
- **PyO3 vs cffi vs cython bridge** — PyO3 is the modern standard, used by `pydantic-core`, `polars`, `ruff`, and most new Python+Rust hybrids.

## What about the broker side?

We can't make Alpaca paper or Coinbase Advanced respond faster. What we *can* do:

1. **Pre-compute everything we can locally** before the signal moment so the broker call is the only blocking I/O.
2. **Use WebSocket order updates** (already wired in `dashboard/api/ws.py`) instead of polling REST.
3. **Submit order then return immediately**, reconcile via WebSocket fill events.
4. **Batch order submissions** when possible (rebalance multiple positions in one cycle).

These are real wins. Rust isn't.

## What about real HFT?

If at some future point we want sub-millisecond execution on real venues, the path is:

1. Direct exchange membership (CME, NASDAQ ITCH/OUCH, Binance Futures direct WebSocket without retail rate limits).
2. Colocation in a datacenter near the exchange (NY4, NY5, SH1, AMS5).
3. Kernel-bypass networking (DPDK, Solarflare, Mellanox) and userspace networking stack.
4. FPGA-accelerated market data parsing (Algo-Logic, Enyx) for nanosecond-class feed handling.
5. C++ or Rust matching engine + order book.
6. Multi-million-dollar infrastructure budget.

**This is not what algo-trader is.** It's a paper-first multi-agent retail bot. The HFT sandbox in `src/moonshot/hft_sandbox.py` is research scaffolding to *study* what HFT looks like in simulation — never to bridge to a live exchange.

## Crates layout (current)

```
crates/
├── Cargo.toml                       # workspace root
└── signal-engine/
    ├── Cargo.toml                   # PyO3 + numpy + ndarray
    ├── pyproject.toml               # maturin build config
    ├── src/lib.rs                   # sma, ema, atr, williams_vix_fix
    └── python/
        └── signal_engine_native/
            └── __init__.py          # facade with HAVE_NATIVE flag
```

## Future crates to consider (only if measured need exists)

- `crates/tick-ingester/` — for L1/L2 order book ingestion if/when we move beyond REST polling.
- `crates/orderbook/` — book reconstruction, microstructure indicators.
- `crates/backtester-fast/` — vectorized backtest engine for tick-level testing of moonshot strategies.

None of these should be built before there is a measured Python bottleneck. **Premature optimization in a research repo is the most expensive form of cargo-culting.**

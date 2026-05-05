# Bug Hunt — automated triage

Each scan adds a section dated by UTC timestamp. Findings are sorted within each
severity tier by (file, line). The operator triages: fix, suppress (with
`# noqa: bug-hunt:<pattern>` on the offending line), or document why it's a
false positive.

Run with: `uv run python scripts/bug_hunt.py` (or `--quick` for a fast loop).

## Scan 2026-05-05 22:30 UTC

_Findings: 0 critical, 3 high, 99 medium, 16 low (total 118)._

### High

| Pattern | File | Line | Detail |
|---|---|---|---|
| `timezone_naive_datetime` | `src/execution/broker.py` | 51 | datetime.utcnow() is tz-naive |
| `timezone_naive_datetime` | `src/execution/broker.py` | 52 | datetime.utcnow() is tz-naive |
| `timezone_naive_datetime` | `src/execution/crypto_broker.py` | 117 | datetime.utcnow() is tz-naive |

### Medium

| Pattern | File | Line | Detail |
|---|---|---|---|
| `mypy:type-arg` | `src/agents/bus.py` | 36 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/agents/bus.py` | 39 | Returning Any from function declared to return "int" |
| `mypy:no-untyped-call` | `src/agents/bus.py` | 51 | Call to untyped function "aclose" in typed context |
| `mypy:type-arg` | `src/agents/governance_agent.py` | 71 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/backtest/metrics.py` | 71 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/backtest/metrics.py` | 96 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/backtest/multi_engine.py` | 52 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/backtest/promotion.py` | 73 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/backtest/sweep.py` | 46 | Missing type arguments for generic type "list" |
| `mypy:var-annotated` | `src/backtest/walk_forward.py` | 95 | Need type annotation for "all_trades" (hint: "all_trades: list[<type>] = ...") |
| `bandit:B104` | `src/config.py` | 50 | Possible binding to all interfaces. |
| `bandit:B310` | `src/data/congress.py` | 75 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-any-return` | `src/data/congress.py` | 76 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/congress.py` | 161 | Missing type arguments for generic type "dict" |
| `bandit:B310` | `src/data/crypto_wallets.py` | 83 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-any-return` | `src/data/crypto_wallets.py` | 84 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 131 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 235 | Missing type arguments for generic type "list" |
| `bandit:B310` | `src/data/funding.py` | 67 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `look_ahead_iloc_minus_1` | `src/data/funding.py` | 141 | df/series.iloc[-1] outside generate_signals(): may leak future bar into training/feature-prep code |
| `mypy:no-any-return` | `src/data/loader.py` | 70 | Returning Any from function declared to return "bool" |
| `mypy:union-attr` | `src/data/loader.py` | 92 | Item "dict[str, Any]" of "BarSet \| dict[str, Any]" has no attribute "df" |
| `bandit:B310` | `src/data/loader.py` | 163 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-any-return` | `src/data/loader.py` | 164 | Returning Any from function declared to return "object \| None" |
| `mypy:type-arg` | `src/data/loader.py` | 243 | Missing type arguments for generic type "tuple" |
| `mypy:no-any-return` | `src/data/loader.py` | 356 | Returning Any from function declared to return "bool" |
| `mypy:unused-ignore` | `src/data/macro_deal_extractor.py` | 346 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/data/news_research.py` | 170 | Missing type arguments for generic type "dict" |
| `bandit:B310` | `src/data/sec_insider.py` | 77 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-any-return` | `src/data/sec_insider.py` | 78 | Returning Any from function declared to return "bytes" |
| `bandit:B314` | `src/data/sec_insider.py` | 166 | Using xml.etree.ElementTree.fromstring to parse untrusted XML data is known to be vulnerable to XML attacks. Replace xml.etree.ElementTree.fromstring with its defusedxml equivalent function or make... |
| `bandit:B314` | `src/data/sec_insider.py` | 258 | Using xml.etree.ElementTree.fromstring to parse untrusted XML data is known to be vulnerable to XML attacks. Replace xml.etree.ElementTree.fromstring with its defusedxml equivalent function or make... |
| `mypy:unused-ignore` | `src/data/sentiment.py` | 221 | Unused "type: ignore" comment |
| `mypy:import-untyped` | `src/data/universe.py` | 18 | Library stubs not installed for "yaml" |
| `mypy:type-arg` | `src/data/universe.py` | 31 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 20 | Function is missing a type annotation |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 21 | Function is missing a return type annotation |
| `mypy:no-untyped-call` | `src/execution/broker.py` | 86 | Call to untyped function "submit_order" in typed context |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 96 | Function is missing a return type annotation |
| `mypy:arg-type` | `src/execution/broker.py` | 118 | Argument 1 to "float" has incompatible type "Decimal \| None"; expected "str \| Buffer \| SupportsFloat \| SupportsIndex" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 125 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/ml/drift.py` | 24 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/drift.py` | 25 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/features.py` | 56 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/ml/features.py` | 59 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/ml/features.py` | 62 | Missing type arguments for generic type "ndarray" |
| `bandit:B301` | `src/ml/predict.py` | 29 | Pickle and modules that wrap it can be unsafe when used to deserialize untrusted data, possible security issue. |
| `mypy:type-arg` | `src/ml/train.py` | 40 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 41 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 50 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 66 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 83 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 94 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 100 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/aspirational_account.py` | 90 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 52 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 107 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 72 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 82 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 116 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 64 | Missing type arguments for generic type "dict" |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 99 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 138 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 151 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 76 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 85 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 98 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 101 | Missing type arguments for generic type "ndarray" |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 25 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 68 | Missing type arguments for generic type "ndarray" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 98 | Item "ndarray[Any, dtype[floating[Any]]]" of "Any \| ndarray[Any, dtype[floating[Any]]]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 113 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 118 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 227 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 333 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/rl/env.py` | 340 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 342 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 21 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 31 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 44 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/train.py` | 32 | Missing type arguments for generic type "ndarray" |
| `bandit:B310` | `src/observability/discord_alert.py` | 91 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-untyped-def` | `src/observability/logging.py` | 43 | Function is missing a return type annotation |
| `bandit:B310` | `src/research/strategy_scout.py` | 337 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-any-return` | `src/research/strategy_scout.py` | 342 | Returning Any from function declared to return "dict[str, Any] \| list[Any] \| None" |
| `mypy:unused-ignore` | `src/research/strategy_scout.py` | 497 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/risk/limits.py` | 44 | Missing type arguments for generic type "tuple" |
| `mypy:no-untyped-def` | `src/runtime/calendar.py` | 75 | Function is missing a return type annotation |
| `mypy:unused-ignore` | `src/runtime/calendar.py` | 78 | Unused "type: ignore" comment |
| `mypy:no-untyped-call` | `src/runtime/calendar.py` | 99 | Call to untyped function "_try_mcal_nyse" in typed context |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 23 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 25 | Unused "type: ignore" comment |
| `mypy:no-any-return` | `src/strategies/momentum_xs.py` | 59 | Returning Any from function declared to return "bool" |

### Low

| Pattern | File | Line | Detail |
|---|---|---|---|
| `bandit:B404` | `scripts/bug_hunt.py` | 36 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/bug_hunt.py` | 545 | subprocess call - check for execution of untrusted input. |
| `bandit:B110` | `scripts/place_order.py` | 37 | Try, Except, Pass detected. |
| `bandit:B404` | `scripts/smoke_paper.py` | 18 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/smoke_paper.py` | 78 | subprocess call - check for execution of untrusted input. |
| `bandit:B101` | `src/agents/bus.py` | 38 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B101` | `src/agents/bus.py` | 44 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B404` | `src/backtest/cli.py` | 11 | Consider possible security implications associated with the subprocess module. |
| `bandit:B607` | `src/backtest/cli.py` | 32 | Starting a process with a partial executable path |
| `bandit:B603` | `src/backtest/cli.py` | 32 | subprocess call - check for execution of untrusted input. |
| `vulture:dead_code` | `src/backtest/multi_engine.py` | 49 | unused import 'correlation_penalty' (90% confidence) |
| `bandit:B405` | `src/data/sec_insider.py` | 26 | Using xml.etree.ElementTree to parse untrusted XML data is known to be vulnerable to XML attacks. Replace xml.etree.ElementTree with the equivalent defusedxml package, or make sure defusedxml.defus... |
| `vulture:dead_code` | `src/execution/broker.py` | 20 | unused variable 'order_data' (100% confidence) |
| `bandit:B403` | `src/ml/predict.py` | 12 | Consider possible security implications associated with pickle module. |
| `bandit:B311` | `src/moonshot/hft_sandbox.py` | 44 | Standard pseudo-random generators are not suitable for security/cryptographic purposes. |
| `vulture:dead_code` | `src/moonshot/rl/env.py` | 19 | unused import 'gym' (90% confidence) |

### Test-suite health

- 729 tests collected across 70 modules
- Slowest tests:
  - `tests/unit/ml/test_train.py::test_train_model_returns_train_result` — 4.03s
  - `tests/unit/dashboard/test_multi_agent_api.py::test_altdata_insider_empty` — 2.87s
  - `tests/unit/ml/test_predict.py::test_predict_score_in_unit_interval` — 2.83s
  - `tests/unit/ml/test_predict.py::test_predict_score_handles_reordered_columns` — 2.80s
  - `tests/unit/ml/test_predict.py::test_save_and_load_model_roundtrip` — 2.60s
- _note_: coverage db present (22 files tracked)

### Tool versions

- ruff: ruff 0.15.11
- mypy: mypy 1.20.1 (compiled: yes)
- bandit: bandit 1.9.4
- vulture: vulture 2.16

## Scan 2026-05-05 23:09 UTC

_Findings: 0 critical, 1 high, 0 medium, 0 low (total 1)._

### High

| Pattern | File | Line | Detail |
|---|---|---|---|
| `ruff:RUF100` | `src/ml/predict.py` | 22 | Unused `noqa` directive (non-enabled: `S403`) |

### Tool versions

- ruff: ruff 0.15.11
- mypy: mypy 1.20.1 (compiled: yes)
- bandit: bandit 1.9.4
- vulture: vulture 2.16

## Scan 2026-05-05 23:10 UTC

_Findings: 0 critical, 0 high, 91 medium, 16 low (total 107)._

### Medium

| Pattern | File | Line | Detail |
|---|---|---|---|
| `mypy:type-arg` | `src/agents/bus.py` | 36 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/agents/bus.py` | 39 | Returning Any from function declared to return "int" |
| `mypy:no-untyped-call` | `src/agents/bus.py` | 51 | Call to untyped function "aclose" in typed context |
| `mypy:type-arg` | `src/agents/governance_agent.py` | 71 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/backtest/metrics.py` | 80 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/backtest/metrics.py` | 105 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/backtest/multi_engine.py` | 52 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/backtest/promotion.py` | 73 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/backtest/sweep.py` | 46 | Missing type arguments for generic type "list" |
| `mypy:var-annotated` | `src/backtest/walk_forward.py` | 95 | Need type annotation for "all_trades" (hint: "all_trades: list[<type>] = ...") |
| `bandit:B104` | `src/config.py` | 50 | Possible binding to all interfaces. |
| `mypy:no-any-return` | `src/data/congress.py` | 78 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/congress.py` | 163 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/crypto_wallets.py` | 86 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 133 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 237 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/data/loader.py` | 71 | Returning Any from function declared to return "bool" |
| `mypy:union-attr` | `src/data/loader.py` | 93 | Item "dict[str, Any]" of "BarSet \| dict[str, Any]" has no attribute "df" |
| `mypy:no-any-return` | `src/data/loader.py` | 165 | Returning Any from function declared to return "object \| None" |
| `mypy:type-arg` | `src/data/loader.py` | 247 | Missing type arguments for generic type "tuple" |
| `mypy:no-any-return` | `src/data/loader.py` | 360 | Returning Any from function declared to return "bool" |
| `mypy:unused-ignore` | `src/data/macro_deal_extractor.py` | 346 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/data/news_research.py` | 170 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/sec_insider.py` | 84 | Returning Any from function declared to return "bytes" |
| `mypy:unused-ignore` | `src/data/sentiment.py` | 221 | Unused "type: ignore" comment |
| `mypy:import-untyped` | `src/data/universe.py` | 18 | Library stubs not installed for "yaml" |
| `mypy:type-arg` | `src/data/universe.py` | 31 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 20 | Function is missing a type annotation |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 21 | Function is missing a return type annotation |
| `mypy:no-untyped-call` | `src/execution/broker.py` | 86 | Call to untyped function "submit_order" in typed context |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 96 | Function is missing a return type annotation |
| `mypy:arg-type` | `src/execution/broker.py` | 118 | Argument 1 to "float" has incompatible type "Decimal \| None"; expected "str \| Buffer \| SupportsFloat \| SupportsIndex" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 125 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/ml/drift.py` | 24 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/drift.py` | 25 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/features.py` | 56 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/ml/features.py` | 59 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/ml/features.py` | 62 | Missing type arguments for generic type "ndarray" |
| `bandit:B301` | `src/ml/predict.py` | 74 | Pickle and modules that wrap it can be unsafe when used to deserialize untrusted data, possible security issue. |
| `mypy:type-arg` | `src/ml/train.py` | 40 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 41 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 50 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 66 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 83 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 94 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 100 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/aspirational_account.py` | 90 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 52 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 107 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 72 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 82 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 116 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 64 | Missing type arguments for generic type "dict" |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 99 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 138 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 151 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 76 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 85 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 98 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 101 | Missing type arguments for generic type "ndarray" |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 25 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 68 | Missing type arguments for generic type "ndarray" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 98 | Item "ndarray[Any, dtype[floating[Any]]]" of "Any \| ndarray[Any, dtype[floating[Any]]]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 113 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 118 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 227 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 333 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/rl/env.py` | 340 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 342 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 21 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 31 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 44 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/train.py` | 32 | Missing type arguments for generic type "ndarray" |
| `bandit:B310` | `src/net/safe_http.py` | 66 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `bandit:B310` | `src/net/safe_http.py` | 67 | Audit url open for permitted schemes. Allowing use of file:/ or custom schemes is often unexpected. |
| `mypy:no-untyped-def` | `src/observability/logging.py` | 43 | Function is missing a return type annotation |
| `mypy:no-any-return` | `src/research/strategy_scout.py` | 344 | Returning Any from function declared to return "dict[str, Any] \| list[Any] \| None" |
| `mypy:unused-ignore` | `src/research/strategy_scout.py` | 499 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/risk/limits.py` | 44 | Missing type arguments for generic type "tuple" |
| `mypy:no-untyped-def` | `src/runtime/calendar.py` | 75 | Function is missing a return type annotation |
| `mypy:unused-ignore` | `src/runtime/calendar.py` | 78 | Unused "type: ignore" comment |
| `mypy:no-untyped-call` | `src/runtime/calendar.py` | 99 | Call to untyped function "_try_mcal_nyse" in typed context |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 23 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 25 | Unused "type: ignore" comment |
| `mypy:no-any-return` | `src/strategies/momentum_xs.py` | 59 | Returning Any from function declared to return "bool" |

### Low

| Pattern | File | Line | Detail |
|---|---|---|---|
| `bandit:B404` | `scripts/bug_hunt.py` | 36 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/bug_hunt.py` | 559 | subprocess call - check for execution of untrusted input. |
| `bandit:B110` | `scripts/place_order.py` | 37 | Try, Except, Pass detected. |
| `bandit:B404` | `scripts/smoke_paper.py` | 18 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/smoke_paper.py` | 78 | subprocess call - check for execution of untrusted input. |
| `bandit:B101` | `src/agents/bus.py` | 38 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B101` | `src/agents/bus.py` | 44 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B404` | `src/backtest/cli.py` | 11 | Consider possible security implications associated with the subprocess module. |
| `bandit:B607` | `src/backtest/cli.py` | 32 | Starting a process with a partial executable path |
| `bandit:B603` | `src/backtest/cli.py` | 32 | subprocess call - check for execution of untrusted input. |
| `vulture:dead_code` | `src/backtest/multi_engine.py` | 49 | unused import 'correlation_penalty' (90% confidence) |
| `bandit:B405` | `src/data/sec_insider.py` | 26 | Using xml.etree.ElementTree to parse untrusted XML data is known to be vulnerable to XML attacks. Replace xml.etree.ElementTree with the equivalent defusedxml package, or make sure defusedxml.defus... |
| `vulture:dead_code` | `src/execution/broker.py` | 20 | unused variable 'order_data' (100% confidence) |
| `bandit:B403` | `src/ml/predict.py` | 22 | Consider possible security implications associated with pickle module. |
| `bandit:B311` | `src/moonshot/hft_sandbox.py` | 44 | Standard pseudo-random generators are not suitable for security/cryptographic purposes. |
| `vulture:dead_code` | `src/moonshot/rl/env.py` | 19 | unused import 'gym' (90% confidence) |

### Test-suite health

- 733 tests collected across 70 modules
- _note_: coverage db present (22 files tracked)

### Tool versions

- ruff: ruff 0.15.11
- mypy: mypy 1.20.1 (compiled: yes)
- bandit: bandit 1.9.4
- vulture: vulture 2.16

## Scan 2026-05-05 23:20 UTC

_Findings: 0 critical, 7 high, 89 medium, 15 low (total 111)._

### High

| Pattern | File | Line | Detail |
|---|---|---|---|
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 24 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 33 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 40 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 48 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 53 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 58 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |
| `test_against_real_network` | `tests/unit/net/test_safe_http.py` | 65 | test calls urllib.request.urlopen / requests directly without monkeypatch in scope — likely hits real network |

### Medium

| Pattern | File | Line | Detail |
|---|---|---|---|
| `mypy:type-arg` | `src/agents/bus.py` | 36 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/agents/bus.py` | 39 | Returning Any from function declared to return "int" |
| `mypy:no-untyped-call` | `src/agents/bus.py` | 51 | Call to untyped function "aclose" in typed context |
| `mypy:type-arg` | `src/agents/governance_agent.py` | 71 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/backtest/metrics.py` | 80 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/backtest/metrics.py` | 105 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/backtest/multi_engine.py` | 52 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/backtest/promotion.py` | 73 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/backtest/sweep.py` | 46 | Missing type arguments for generic type "list" |
| `mypy:var-annotated` | `src/backtest/walk_forward.py` | 95 | Need type annotation for "all_trades" (hint: "all_trades: list[<type>] = ...") |
| `bandit:B104` | `src/config.py` | 50 | Possible binding to all interfaces. |
| `mypy:no-any-return` | `src/data/congress.py` | 78 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/congress.py` | 163 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/crypto_wallets.py` | 86 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 133 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 237 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/data/loader.py` | 71 | Returning Any from function declared to return "bool" |
| `mypy:union-attr` | `src/data/loader.py` | 93 | Item "dict[str, Any]" of "BarSet \| dict[str, Any]" has no attribute "df" |
| `mypy:no-any-return` | `src/data/loader.py` | 165 | Returning Any from function declared to return "object \| None" |
| `mypy:type-arg` | `src/data/loader.py` | 247 | Missing type arguments for generic type "tuple" |
| `mypy:no-any-return` | `src/data/loader.py` | 360 | Returning Any from function declared to return "bool" |
| `mypy:unused-ignore` | `src/data/macro_deal_extractor.py` | 346 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/data/news_research.py` | 170 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/sec_insider.py` | 89 | Returning Any from function declared to return "bytes" |
| `mypy:unused-ignore` | `src/data/sentiment.py` | 221 | Unused "type: ignore" comment |
| `mypy:import-untyped` | `src/data/universe.py` | 18 | Library stubs not installed for "yaml" |
| `mypy:type-arg` | `src/data/universe.py` | 31 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 20 | Function is missing a type annotation |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 21 | Function is missing a return type annotation |
| `mypy:no-untyped-call` | `src/execution/broker.py` | 86 | Call to untyped function "submit_order" in typed context |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 96 | Function is missing a return type annotation |
| `mypy:arg-type` | `src/execution/broker.py` | 118 | Argument 1 to "float" has incompatible type "Decimal \| None"; expected "str \| Buffer \| SupportsFloat \| SupportsIndex" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 125 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/ml/drift.py` | 24 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/drift.py` | 25 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/features.py` | 56 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/ml/features.py` | 59 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/ml/features.py` | 62 | Missing type arguments for generic type "ndarray" |
| `bandit:B301` | `src/ml/predict.py` | 74 | Pickle and modules that wrap it can be unsafe when used to deserialize untrusted data, possible security issue. |
| `mypy:type-arg` | `src/ml/train.py` | 40 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 41 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 50 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 66 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 83 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 94 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 100 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/aspirational_account.py` | 90 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 52 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 107 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 72 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 82 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 116 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 64 | Missing type arguments for generic type "dict" |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 99 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 138 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 151 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 76 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 85 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 98 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 101 | Missing type arguments for generic type "ndarray" |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 25 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 68 | Missing type arguments for generic type "ndarray" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 98 | Item "ndarray[Any, dtype[floating[Any]]]" of "Any \| ndarray[Any, dtype[floating[Any]]]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 113 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 118 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 227 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 333 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/rl/env.py` | 340 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 342 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 21 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 31 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 44 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/train.py` | 32 | Missing type arguments for generic type "ndarray" |
| `mypy:no-untyped-def` | `src/observability/logging.py` | 43 | Function is missing a return type annotation |
| `mypy:no-any-return` | `src/research/strategy_scout.py` | 344 | Returning Any from function declared to return "dict[str, Any] \| list[Any] \| None" |
| `mypy:unused-ignore` | `src/research/strategy_scout.py` | 499 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/risk/limits.py` | 44 | Missing type arguments for generic type "tuple" |
| `mypy:no-untyped-def` | `src/runtime/calendar.py` | 75 | Function is missing a return type annotation |
| `mypy:unused-ignore` | `src/runtime/calendar.py` | 78 | Unused "type: ignore" comment |
| `mypy:no-untyped-call` | `src/runtime/calendar.py` | 99 | Call to untyped function "_try_mcal_nyse" in typed context |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 23 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 25 | Unused "type: ignore" comment |
| `mypy:no-any-return` | `src/strategies/momentum_xs.py` | 59 | Returning Any from function declared to return "bool" |

### Low

| Pattern | File | Line | Detail |
|---|---|---|---|
| `bandit:B404` | `scripts/bug_hunt.py` | 36 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/bug_hunt.py` | 559 | subprocess call - check for execution of untrusted input. |
| `bandit:B110` | `scripts/place_order.py` | 36 | Try, Except, Pass detected. |
| `bandit:B404` | `scripts/smoke_paper.py` | 18 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/smoke_paper.py` | 78 | subprocess call - check for execution of untrusted input. |
| `bandit:B101` | `src/agents/bus.py` | 38 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B101` | `src/agents/bus.py` | 44 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B404` | `src/backtest/cli.py` | 11 | Consider possible security implications associated with the subprocess module. |
| `bandit:B607` | `src/backtest/cli.py` | 32 | Starting a process with a partial executable path |
| `bandit:B603` | `src/backtest/cli.py` | 32 | subprocess call - check for execution of untrusted input. |
| `vulture:dead_code` | `src/backtest/multi_engine.py` | 49 | unused import 'correlation_penalty' (90% confidence) |
| `vulture:dead_code` | `src/execution/broker.py` | 20 | unused variable 'order_data' (100% confidence) |
| `bandit:B403` | `src/ml/predict.py` | 22 | Consider possible security implications associated with pickle module. |
| `bandit:B311` | `src/moonshot/hft_sandbox.py` | 44 | Standard pseudo-random generators are not suitable for security/cryptographic purposes. |
| `vulture:dead_code` | `src/moonshot/rl/env.py` | 19 | unused import 'gym' (90% confidence) |

### Test-suite health

- 758 tests collected across 72 modules
- Slowest tests:
  - `tests/unit/ml/test_train.py::test_train_model_returns_train_result` — 3.78s
  - `tests/unit/dashboard/test_multi_agent_api.py::test_altdata_insider_empty` — 3.70s
  - `tests/unit/ml/test_predict.py::test_save_and_load_model_roundtrip` — 2.64s
  - `tests/unit/ml/test_predict.py::test_predict_score_in_unit_interval` — 2.54s
  - `tests/unit/ml/test_predict.py::test_load_model_requires_sidecar` — 2.51s
- _note_: coverage db present (22 files tracked)

### Tool versions

- ruff: ruff 0.15.11
- mypy: mypy 1.20.1 (compiled: yes)
- bandit: bandit 1.9.4
- vulture: vulture 2.16

## Scan 2026-05-05 23:21 UTC

_Findings: 0 critical, 0 high, 89 medium, 15 low (total 104)._

### Medium

| Pattern | File | Line | Detail |
|---|---|---|---|
| `mypy:type-arg` | `src/agents/bus.py` | 36 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/agents/bus.py` | 39 | Returning Any from function declared to return "int" |
| `mypy:no-untyped-call` | `src/agents/bus.py` | 51 | Call to untyped function "aclose" in typed context |
| `mypy:type-arg` | `src/agents/governance_agent.py` | 71 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/backtest/metrics.py` | 80 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/backtest/metrics.py` | 105 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/backtest/multi_engine.py` | 52 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/backtest/promotion.py` | 73 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/backtest/sweep.py` | 46 | Missing type arguments for generic type "list" |
| `mypy:var-annotated` | `src/backtest/walk_forward.py` | 95 | Need type annotation for "all_trades" (hint: "all_trades: list[<type>] = ...") |
| `bandit:B104` | `src/config.py` | 50 | Possible binding to all interfaces. |
| `mypy:no-any-return` | `src/data/congress.py` | 78 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/congress.py` | 163 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/crypto_wallets.py` | 86 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 133 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 237 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/data/loader.py` | 71 | Returning Any from function declared to return "bool" |
| `mypy:union-attr` | `src/data/loader.py` | 93 | Item "dict[str, Any]" of "BarSet \| dict[str, Any]" has no attribute "df" |
| `mypy:no-any-return` | `src/data/loader.py` | 165 | Returning Any from function declared to return "object \| None" |
| `mypy:type-arg` | `src/data/loader.py` | 247 | Missing type arguments for generic type "tuple" |
| `mypy:no-any-return` | `src/data/loader.py` | 360 | Returning Any from function declared to return "bool" |
| `mypy:unused-ignore` | `src/data/macro_deal_extractor.py` | 346 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/data/news_research.py` | 170 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/sec_insider.py` | 89 | Returning Any from function declared to return "bytes" |
| `mypy:unused-ignore` | `src/data/sentiment.py` | 221 | Unused "type: ignore" comment |
| `mypy:import-untyped` | `src/data/universe.py` | 18 | Library stubs not installed for "yaml" |
| `mypy:type-arg` | `src/data/universe.py` | 31 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 20 | Function is missing a type annotation |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 21 | Function is missing a return type annotation |
| `mypy:no-untyped-call` | `src/execution/broker.py` | 86 | Call to untyped function "submit_order" in typed context |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 96 | Function is missing a return type annotation |
| `mypy:arg-type` | `src/execution/broker.py` | 118 | Argument 1 to "float" has incompatible type "Decimal \| None"; expected "str \| Buffer \| SupportsFloat \| SupportsIndex" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 125 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/ml/drift.py` | 24 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/drift.py` | 25 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/features.py` | 56 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/ml/features.py` | 59 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/ml/features.py` | 62 | Missing type arguments for generic type "ndarray" |
| `bandit:B301` | `src/ml/predict.py` | 74 | Pickle and modules that wrap it can be unsafe when used to deserialize untrusted data, possible security issue. |
| `mypy:type-arg` | `src/ml/train.py` | 40 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 41 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 50 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 66 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 83 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 94 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 100 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/aspirational_account.py` | 90 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 52 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 107 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 72 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 82 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 116 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 64 | Missing type arguments for generic type "dict" |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 99 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 138 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 151 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 76 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 85 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 98 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 101 | Missing type arguments for generic type "ndarray" |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 25 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 68 | Missing type arguments for generic type "ndarray" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 98 | Item "ndarray[Any, dtype[floating[Any]]]" of "Any \| ndarray[Any, dtype[floating[Any]]]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 113 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 118 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 227 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 228 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 246 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 333 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/rl/env.py` | 340 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 342 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 21 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 31 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 44 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/train.py` | 32 | Missing type arguments for generic type "ndarray" |
| `mypy:no-untyped-def` | `src/observability/logging.py` | 43 | Function is missing a return type annotation |
| `mypy:no-any-return` | `src/research/strategy_scout.py` | 344 | Returning Any from function declared to return "dict[str, Any] \| list[Any] \| None" |
| `mypy:unused-ignore` | `src/research/strategy_scout.py` | 499 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/risk/limits.py` | 44 | Missing type arguments for generic type "tuple" |
| `mypy:no-untyped-def` | `src/runtime/calendar.py` | 75 | Function is missing a return type annotation |
| `mypy:unused-ignore` | `src/runtime/calendar.py` | 78 | Unused "type: ignore" comment |
| `mypy:no-untyped-call` | `src/runtime/calendar.py` | 99 | Call to untyped function "_try_mcal_nyse" in typed context |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 23 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 25 | Unused "type: ignore" comment |
| `mypy:no-any-return` | `src/strategies/momentum_xs.py` | 59 | Returning Any from function declared to return "bool" |

### Low

| Pattern | File | Line | Detail |
|---|---|---|---|
| `bandit:B404` | `scripts/bug_hunt.py` | 36 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/bug_hunt.py` | 570 | subprocess call - check for execution of untrusted input. |
| `bandit:B110` | `scripts/place_order.py` | 36 | Try, Except, Pass detected. |
| `bandit:B404` | `scripts/smoke_paper.py` | 18 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/smoke_paper.py` | 78 | subprocess call - check for execution of untrusted input. |
| `bandit:B101` | `src/agents/bus.py` | 38 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B101` | `src/agents/bus.py` | 44 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B404` | `src/backtest/cli.py` | 11 | Consider possible security implications associated with the subprocess module. |
| `bandit:B607` | `src/backtest/cli.py` | 32 | Starting a process with a partial executable path |
| `bandit:B603` | `src/backtest/cli.py` | 32 | subprocess call - check for execution of untrusted input. |
| `vulture:dead_code` | `src/backtest/multi_engine.py` | 49 | unused import 'correlation_penalty' (90% confidence) |
| `vulture:dead_code` | `src/execution/broker.py` | 20 | unused variable 'order_data' (100% confidence) |
| `bandit:B403` | `src/ml/predict.py` | 22 | Consider possible security implications associated with pickle module. |
| `bandit:B311` | `src/moonshot/hft_sandbox.py` | 44 | Standard pseudo-random generators are not suitable for security/cryptographic purposes. |
| `vulture:dead_code` | `src/moonshot/rl/env.py` | 19 | unused import 'gym' (90% confidence) |

### Test-suite health

- 758 tests collected across 72 modules
- Slowest tests:
  - `tests/unit/ml/test_train.py::test_train_model_returns_train_result` — 3.82s
  - `tests/unit/dashboard/test_multi_agent_api.py::test_altdata_insider_empty` — 3.43s
  - `tests/unit/ml/test_predict.py::test_predict_score_in_unit_interval` — 2.57s
  - `tests/unit/ml/test_predict.py::test_save_and_load_model_roundtrip` — 2.55s
  - `tests/unit/ml/test_predict.py::test_predict_score_handles_reordered_columns` — 2.52s
- _note_: coverage db present (22 files tracked)

### Tool versions

- ruff: ruff 0.15.11
- mypy: mypy 1.20.1 (compiled: yes)
- bandit: bandit 1.9.4
- vulture: vulture 2.16

## Scan 2026-05-05 23:34 UTC

_Findings: 0 critical, 0 high, 89 medium, 15 low (total 104)._

### Medium

| Pattern | File | Line | Detail |
|---|---|---|---|
| `mypy:type-arg` | `src/agents/bus.py` | 36 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/agents/bus.py` | 39 | Returning Any from function declared to return "int" |
| `mypy:no-untyped-call` | `src/agents/bus.py` | 51 | Call to untyped function "aclose" in typed context |
| `mypy:type-arg` | `src/agents/governance_agent.py` | 71 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/backtest/metrics.py` | 80 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/backtest/metrics.py` | 105 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/backtest/multi_engine.py` | 52 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/backtest/promotion.py` | 73 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/backtest/sweep.py` | 46 | Missing type arguments for generic type "list" |
| `mypy:var-annotated` | `src/backtest/walk_forward.py` | 95 | Need type annotation for "all_trades" (hint: "all_trades: list[<type>] = ...") |
| `bandit:B104` | `src/config.py` | 50 | Possible binding to all interfaces. |
| `mypy:no-any-return` | `src/data/congress.py` | 78 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/congress.py` | 163 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/crypto_wallets.py` | 86 | Returning Any from function declared to return "bytes" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 133 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/data/crypto_wallets.py` | 237 | Missing type arguments for generic type "list" |
| `mypy:no-any-return` | `src/data/loader.py` | 71 | Returning Any from function declared to return "bool" |
| `mypy:union-attr` | `src/data/loader.py` | 93 | Item "dict[str, Any]" of "BarSet \| dict[str, Any]" has no attribute "df" |
| `mypy:no-any-return` | `src/data/loader.py` | 165 | Returning Any from function declared to return "object \| None" |
| `mypy:type-arg` | `src/data/loader.py` | 247 | Missing type arguments for generic type "tuple" |
| `mypy:no-any-return` | `src/data/loader.py` | 360 | Returning Any from function declared to return "bool" |
| `mypy:unused-ignore` | `src/data/macro_deal_extractor.py` | 346 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/data/news_research.py` | 170 | Missing type arguments for generic type "dict" |
| `mypy:no-any-return` | `src/data/sec_insider.py` | 89 | Returning Any from function declared to return "bytes" |
| `mypy:unused-ignore` | `src/data/sentiment.py` | 221 | Unused "type: ignore" comment |
| `mypy:import-untyped` | `src/data/universe.py` | 18 | Library stubs not installed for "yaml" |
| `mypy:type-arg` | `src/data/universe.py` | 31 | Missing type arguments for generic type "dict" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 20 | Function is missing a type annotation |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 21 | Function is missing a return type annotation |
| `mypy:no-untyped-call` | `src/execution/broker.py` | 94 | Call to untyped function "submit_order" in typed context |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 104 | Function is missing a return type annotation |
| `mypy:arg-type` | `src/execution/broker.py` | 126 | Argument 1 to "float" has incompatible type "Decimal \| None"; expected "str \| Buffer \| SupportsFloat \| SupportsIndex" |
| `mypy:no-untyped-def` | `src/execution/broker.py` | 133 | Function is missing a type annotation for one or more parameters |
| `mypy:type-arg` | `src/ml/drift.py` | 24 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/drift.py` | 25 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/features.py` | 56 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/ml/features.py` | 59 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/ml/features.py` | 62 | Missing type arguments for generic type "ndarray" |
| `bandit:B301` | `src/ml/predict.py` | 74 | Pickle and modules that wrap it can be unsafe when used to deserialize untrusted data, possible security issue. |
| `mypy:type-arg` | `src/ml/train.py` | 40 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 41 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/ml/train.py` | 50 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 66 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 83 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 94 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/ml/train.py` | 100 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/aspirational_account.py` | 94 | Returning Any from function declared to return "float" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 52 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/copy_shadow.py` | 107 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 75 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 85 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/hft_sandbox.py` | 119 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 67 | Missing type arguments for generic type "dict" |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 102 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/llm_discretionary.py` | 141 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/llm_discretionary.py` | 154 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 81 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 90 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 103 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/agent.py` | 106 | Missing type arguments for generic type "ndarray" |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/moonshot/rl/env.py` | 25 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 71 | Missing type arguments for generic type "ndarray" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 101 | Item "ndarray[Any, dtype[floating[Any]]]" of "Any \| ndarray[Any, dtype[floating[Any]]]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 116 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:union-attr` | `src/moonshot/rl/env.py` | 121 | Item "ndarray[Any, Any]" of "Any \| ndarray[Any, Any]" has no attribute "fillna" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 230 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 231 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 231 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 249 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 249 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 336 | Missing type arguments for generic type "ndarray" |
| `mypy:no-any-return` | `src/moonshot/rl/env.py` | 343 | Returning Any from function declared to return "ndarray[Any, Any]" |
| `mypy:type-arg` | `src/moonshot/rl/env.py` | 345 | Missing type arguments for generic type "dict" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 25 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 35 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/evaluate.py` | 48 | Missing type arguments for generic type "ndarray" |
| `mypy:type-arg` | `src/moonshot/rl/train.py` | 35 | Missing type arguments for generic type "ndarray" |
| `mypy:no-untyped-def` | `src/observability/logging.py` | 43 | Function is missing a return type annotation |
| `mypy:no-any-return` | `src/research/strategy_scout.py` | 344 | Returning Any from function declared to return "dict[str, Any] \| list[Any] \| None" |
| `mypy:unused-ignore` | `src/research/strategy_scout.py` | 499 | Unused "type: ignore" comment |
| `mypy:type-arg` | `src/risk/limits.py` | 44 | Missing type arguments for generic type "tuple" |
| `mypy:no-untyped-def` | `src/runtime/calendar.py` | 75 | Function is missing a return type annotation |
| `mypy:unused-ignore` | `src/runtime/calendar.py` | 78 | Unused "type: ignore" comment |
| `mypy:no-untyped-call` | `src/runtime/calendar.py` | 99 | Call to untyped function "_try_mcal_nyse" in typed context |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 23 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 24 | Unused "type: ignore" comment |
| `mypy:unused-ignore` | `src/signals/indicators.py` | 25 | Unused "type: ignore" comment |
| `mypy:no-any-return` | `src/strategies/momentum_xs.py` | 59 | Returning Any from function declared to return "bool" |

### Low

| Pattern | File | Line | Detail |
|---|---|---|---|
| `bandit:B404` | `scripts/bug_hunt.py` | 36 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/bug_hunt.py` | 570 | subprocess call - check for execution of untrusted input. |
| `bandit:B110` | `scripts/place_order.py` | 42 | Try, Except, Pass detected. |
| `bandit:B404` | `scripts/smoke_paper.py` | 18 | Consider possible security implications associated with the subprocess module. |
| `bandit:B603` | `scripts/smoke_paper.py` | 78 | subprocess call - check for execution of untrusted input. |
| `bandit:B101` | `src/agents/bus.py` | 38 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B101` | `src/agents/bus.py` | 44 | Use of assert detected. The enclosed code will be removed when compiling to optimised byte code. |
| `bandit:B404` | `src/backtest/cli.py` | 11 | Consider possible security implications associated with the subprocess module. |
| `bandit:B607` | `src/backtest/cli.py` | 32 | Starting a process with a partial executable path |
| `bandit:B603` | `src/backtest/cli.py` | 32 | subprocess call - check for execution of untrusted input. |
| `vulture:dead_code` | `src/backtest/multi_engine.py` | 49 | unused import 'correlation_penalty' (90% confidence) |
| `vulture:dead_code` | `src/execution/broker.py` | 20 | unused variable 'order_data' (100% confidence) |
| `bandit:B403` | `src/ml/predict.py` | 22 | Consider possible security implications associated with pickle module. |
| `bandit:B311` | `src/moonshot/hft_sandbox.py` | 47 | Standard pseudo-random generators are not suitable for security/cryptographic purposes. |
| `vulture:dead_code` | `src/moonshot/rl/env.py` | 19 | unused import 'gym' (90% confidence) |

### Test-suite health

- 766 tests collected across 73 modules
- Slowest tests:
  - `tests/unit/dashboard/test_multi_agent_api.py::test_altdata_insider_empty` — 3.84s
  - `tests/unit/ml/test_train.py::test_train_model_returns_train_result` — 3.73s
  - `tests/unit/ml/test_predict.py::test_save_and_load_model_roundtrip` — 2.73s
  - `tests/unit/ml/test_predict.py::test_load_model_rejects_hash_mismatch` — 2.54s
  - `tests/unit/ml/test_predict.py::test_save_model_writes_sidecar` — 2.51s
- _note_: coverage db present (22 files tracked)

### Tool versions

- ruff: ruff 0.15.11
- mypy: mypy 1.20.1 (compiled: yes)
- bandit: bandit 1.9.4
- vulture: vulture 2.16


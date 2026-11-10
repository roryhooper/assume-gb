<!--
SPDX-FileCopyrightText: ASSUME Developers

SPDX-License-Identifier: MIT
-->

# Sequential DA/ID Markets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two sequential pay-as-clear energy auctions (DA and ID) for the same delivery day, with per-market forecasts, per-market accounting, forced position corrections, a switch-price thermal strategy, and a one-week example scenario.

**Architecture:** Generic building blocks in the framework (per-market forecasts with fallback, per-market volume/cashflow/cost/profit series, a forced-correction primitive with one strategy pricing hook), minimal fixes to existing strategies, one new thermal strategy bidding through `complex_clearing`, and a seeded input generator for the example.

**Tech Stack:** Python 3.11–3.14, pandas >=3,<3.1, numpy, pyomo + highspy (`complex_clearing`), pytest (`asyncio_mode=auto`), FastSeries/FastIndex.

**Spec:** `docs/superpowers/specs/2026-11-10-sequential-da-id-markets-design.md` — read it first; every task cites its sections.

## Global Constraints

- Every new `.py` file starts with the two-line SPDX header (`# SPDX-FileCopyrightText: ASSUME Developers`, `# SPDX-License-Identifier: MIT`); `pre-commit run --files <changed>` must pass on every commit (reuse lint, ruff, codespell).
- No `.license` sidecar files anywhere; generated example data is covered by a `REUSE.toml` annotation (Task 13).
- Every new or changed signature takes `market_id: str | None = None`; `None` must reproduce today's behaviour exactly (spec §3).
- Single-market examples must be unchanged except the fixes listed in spec §12; Task 15 verifies this.
- New per-market output keys follow exactly: `energy_<market_id>`, `energy_cashflow_<market_id>`, `energy_total_costs_<market_id>`, `energy_profit_<market_id>`, `energy_deviation` (spec §5).
- New registry names: `powerplant_energy_heuristic_flexable_hourly_linked`, and `demand_energy_naive` repointed to `DemandEnergyNaiveStrategy`.
- Tests run with `pytest -m "not slow and not require_learning and not require_network"` unless a step says otherwise; the integration test is marked `@pytest.mark.slow`.
- User-facing changes get a `docs/source/release_notes.rst` entry (Task 16 collects them).

## Review Focus

Failure modes the spec implies but no obvious test exercises; each line's test is added to the owning task:

1. A correction buy priced exactly at `maximum_bid_price` must not be clipped or rejected by `validate_orderbook` (equality is allowed; only `>` clips) → Task 8.
2. A renewable whose DA bids only partly cleared (position < new ID max) must place **no** correction bid, only economic ones — no spurious price-taker buy → Task 7.
3. A thermal position strictly between 0 and min load (partial DA block artifact) must be corrected up to *m* when feasible, else to 0 → Task 7.
4. ID availability collapse to *U* < *m* with a large position: the correction must buy back the **whole** position (feasible set {0}) → Task 7.
5. Bidding the first delivery hour of the index (`get_output_before` / `get_operation_time` at `index[0]`) must not raise or mis-count operation time → Task 12.

---

### Task 1: Per-market forecasts in the forecaster

**Files:**
- Modify: `assume/common/forecaster.py` (`UnitForecaster`, `DemandForecaster`)
- Test: `tests/test_forecast_interface.py`

**Interfaces:**
- Produces: `UnitForecaster.__init__(..., availability_forecast: dict[str, ForecastSeries] | None = None)`; `UnitForecaster.get_availability(market_id: str | None = None) -> FastSeries`; `DemandForecaster.__init__(..., demand_forecast: dict[str, ForecastSeries] | None = None)`; `DemandForecaster.get_demand(market_id: str | None = None) -> FastSeries`. Attributes `availability_forecast` / `demand_forecast` are `dict[str, FastSeries]` (empty dict when not given).

- [ ] **Step 1: Write failing tests** in `tests/test_forecast_interface.py`:

```python
def test_get_availability_fallback():
    # forecaster with availability=0.8 everywhere, availability_forecast={"DA": 0.5}
    assert fc.get_availability("DA")[0] == 0.5
    assert fc.get_availability("ID")[0] == 0.8   # no ID entry -> realised
    assert fc.get_availability()[0] == 0.8       # None -> realised

def test_availability_forecast_validated():
    # availability_forecast={"DA": 1.2} raises ValidationError

def test_get_demand_fallback():
    # DemandForecaster(demand=-100, demand_forecast={"DA": -90})
    assert fc.get_demand("DA")[0] == -90
    assert fc.get_demand()[0] == -100

def test_demand_forecast_validated():
    # demand_forecast={"DA": +5} raises ValidationError
```

- [ ] **Step 2: Run** `pytest tests/test_forecast_interface.py -v` — new tests FAIL (unexpected keyword / missing attribute).
- [ ] **Step 3: Implement** the two constructor arguments (converted via the existing `_dict_to_series`, validated per series with the same checks as `availability` / `demand`) and the two getters.
- [ ] **Step 4: Run** `pytest tests/test_forecast_interface.py -v` — PASS; also `pytest tests/test_forecaster_units.py -v` if present, else the full non-slow suite for forecaster regressions.
- [ ] **Step 5: Commit** `feat(forecaster): per-market availability and demand forecasts with fallback`

### Task 2: Loader reads per-market forecast files

**Files:**
- Modify: `assume/scenario/loader_csv.py` (the block that loads `availability_df`/`demand_df` and constructs `unit_forecasts`, ~lines 690–800)
- Test: `tests/test_loader_csv.py`, fixture folder `tests/fixtures/per_market_forecasts/`

**Interfaces:**
- Consumes: Task 1 constructor args.
- Produces: optional input files `availability_<market_id>_df.csv`, `demand_<market_id>_df.csv` per market id in `markets_config`, same layout/normalisation as the realised files (spec §4.2).

- [ ] **Step 1: Create fixture** `tests/fixtures/per_market_forecasts/` — minimal copy of an existing small fixture (or hand-written: `config.yaml` with markets `DA` and `ID`, one powerplant, one demand unit, `availability_df.csv`, `availability_DA_df.csv`, `demand_df.csv`, `demand_DA_df.csv`; no ID files).
- [ ] **Step 2: Write failing test** in `tests/test_loader_csv.py`:

```python
def test_loader_per_market_forecasts(world_fixture...):
    # after load_scenario_folder on the fixture:
    # powerplant forecaster: get_availability("DA") matches availability_DA_df column,
    #   get_availability("ID") matches availability_df column (fallback)
    # demand forecaster: get_demand("DA") == -abs(demand_DA_df col); get_demand("ID") falls back
```

Follow the setup pattern of the existing `test_csv_loader_*` tests.

- [ ] **Step 3: Run** `pytest tests/test_loader_csv.py -v` — new test FAILS.
- [ ] **Step 4: Implement:** loop `config["markets_config"].keys()`, `load_file(..., file_name=f"availability_{market_id}_df")` and `f"demand_{market_id}_df"`; apply the same `normalize_availability` warning path and `-df.abs()` sign handling as the realised files; pass per-unit column dicts into `PowerplantForecaster` / `DemandForecaster` / `UnitForecaster` constructors (only units present as columns get an entry).
- [ ] **Step 5: Run** `pytest tests/test_loader_csv.py -v` — PASS.
- [ ] **Step 6: Commit** `feat(loader): load optional per-market availability and demand forecast files`

### Task 3: Market-aware naive price forecast

**Files:**
- Modify: `assume/common/forecast_algorithms.py` (`calculate_max_power`, `calculate_sum_demand`, `calculate_naive_price_inelastic`, `calculate_naive_price_elastic`, `calculate_naive_residual_load`)
- Test: `tests/test_forecasts.py` or `tests/test_forecast_interface.py` (wherever `calculate_naive_price` is tested today — extend that file)

**Interfaces:**
- Consumes: Task 1 getters.
- Produces: `calculate_max_power(units, index=None, market_id=None)`, `calculate_sum_demand(demand_units, exchange_units, market_id=None)`; the three forecast algorithms pass `config.market_id`. Default `None` keeps the congestion-signal and renewable-utilisation callers unchanged (spec §4.3).

- [ ] **Step 1: Write failing test:**

```python
def test_naive_price_forecast_differs_per_market():
    # two market configs DA/ID; one powerplant with availability_forecast {"DA": 0.5}
    # (realised 1.0) and one demand unit with demand_forecast {"DA": -80} (realised -100)
    # -> price forecast dict has DA != ID, ID equals the single-market result
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the `market_id` plumbing (getter calls inside `calculate_max_power` / `calculate_sum_demand`; `config.market_id` at the three call sites).
- [ ] **Step 4: Run** the test file and `pytest tests/test_loader_csv.py -v` (forecast init path) — PASS.
- [ ] **Step 5: Commit** `feat(forecast): naive price and residual load use per-market forecasts`

### Task 4: Per-market volumes and cashflow

**Files:**
- Modify: `assume/common/base.py` (`BaseUnit.set_dispatch_plan`, `BaseUnit.calculate_cashflow`)
- Test: `tests/test_baseunit.py`

**Interfaces:**
- Consumes: orders carry `order["market_id"]` (already set in `UnitsOperator.handle_market_feedback`; tests set it manually).
- Produces: `outputs[f"{product_type}_{market_id}"]` accumulates accepted volumes; `outputs[f"{product_type}_cashflow_{market_id}"]` accumulates cashflow. Orders without a `market_id` key skip the per-market series (backward compatible).

- [ ] **Step 1: Write failing tests:**

```python
def test_per_market_volume_tracking():
    # two orderbooks, market_id "DA" (vol 100) then "ID" (vol -30), same hour
    # outputs["energy"] == 70; outputs["energy_DA"] == 100; outputs["energy_ID"] == -30

def test_per_market_cashflow_sums_to_total():
    # energy_cashflow == energy_cashflow_DA + energy_cashflow_ID elementwise

def test_order_without_market_id_unchanged():
    # legacy order dict (no market_id): outputs["energy"] updated, no per-market key created
```

- [ ] **Step 2: Run** `pytest tests/test_baseunit.py -v` — new tests FAIL.
- [ ] **Step 3: Implement** in `set_dispatch_plan` and `calculate_cashflow`: `market_id = order.get("market_id")`; when not `None`, mirror the existing accumulation into the suffixed series.
- [ ] **Step 4: Run** `pytest tests/test_baseunit.py tests/test_powerplant.py tests/test_storage.py -v` — PASS (storage override calls `super()`, gets this free).
- [ ] **Step 5: Commit** `feat(units): record accepted volume and cashflow per market`

### Task 5: Operating cost and per-market profit attribution

**Files:**
- Modify: `assume/common/base.py` (`BaseUnit`, `SupportsMinMax`, `BaseUnit.set_dispatch_plan`, `BaseUnit.calculate_cashflow`), `assume/units/demand.py`
- Test: `tests/test_baseunit.py`, `tests/test_demand.py`

**Interfaces:**
- Consumes: Task 4 per-market series.
- Produces: `BaseUnit.calculate_operating_cost(start: datetime, end: datetime) -> np.ndarray` (hourly cost of the current position, both ends inclusive). Default `abs(P_t) * calculate_marginal_cost(t, P_t)`. `SupportsMinMax` override adds `get_starting_costs(op_time)` on off→on transitions of `outputs["energy"]`. `Demand` override returns zeros. `outputs[f"energy_total_costs_{m}"]` and `outputs[f"energy_profit_{m}"]` per spec §5.3.

- [ ] **Step 1: Write failing tests:**

```python
def test_operating_cost_default_and_demand_zero():
    # powerplant: cost == P * mc on running hours; demand unit: zeros everywhere

def test_operating_cost_includes_startups():
    # off->on transition hour carries get_starting_costs(op_time) extra

def test_profit_attribution_sums_to_total():
    # DA sell 100 @ accepted 60, ID buy-back 100 @ accepted 30 (same hour, mc 40):
    # energy_profit_DA + energy_profit_ID == energy_cashflow - operating cost of final position
    # and energy_profit_ID > 0 (cost avoided 40*100 > paid 30*100)

def test_attribution_catches_distant_start_reclassification():
    # position change splits a long off-period so a start >1h later gets cheaper;
    # full-horizon window books the (negative) cost delta to the causing market
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement:** the three `calculate_operating_cost` methods; in `set_dispatch_plan` (energy product types with a `market_id` only) evaluate cost from the first affected hour to `index[-1]` before and after applying volumes, store the difference; at the end of `calculate_cashflow` assign `outputs[f"{pt}_profit_{m}"] = outputs[f"{pt}_cashflow_{m}"] - outputs[f"{pt}_total_costs_{m}"]` (full series).
- [ ] **Step 4: Run** `pytest tests/test_baseunit.py tests/test_demand.py tests/test_powerplant.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(units): per-market profit attribution via sequential operating-cost deltas`

### Task 6: Deviation export and end-of-simulation flush

**Files:**
- Modify: `assume/common/units_operator.py` (`get_actual_dispatch`, new `on_stop`)
- Test: `tests/test_units_operator.py`

**Interfaces:**
- Consumes: Tasks 4–5 outputs.
- Produces: `outputs["energy_deviation"]` = physical − position over executed hours; `valid_outputs` filter gains `"energy_profit"` and `"deviation"` (exact substrings — plain `"profit"` would leak the strategy-level `profit` series); `async def on_stop(self)` calls `write_actual_dispatch(pt)` for every traded product type (mirror `OutputRole.on_stop`, spec §5.1).

- [ ] **Step 1: Write failing tests:**

```python
def test_energy_deviation_written():
    # powerplant position 100, realised availability caps physical at 80
    # after get_actual_dispatch: outputs["energy_deviation"] at that hour == -20

def test_dispatch_export_includes_profit_and_deviation_not_strategy_profit():
    # unit_dispatch dict keys include "energy_profit_DA", "energy_deviation"; exclude "profit"

async def test_on_stop_flushes_final_dispatch():
    # operator with valid orders whose delivery ends before `now`; on_stop sends unit_dispatch
```

Follow the existing async test setup in `tests/test_units_operator.py`.

- [ ] **Step 2: Run** `pytest tests/test_units_operator.py -v` — FAIL.
- [ ] **Step 3: Implement** (snapshot position with `.copy()` before `execute_current_dispatch`; subtract; extend `valid_outputs`; add `on_stop`).
- [ ] **Step 4: Run** `pytest tests/test_units_operator.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(units_operator): export energy deviation and flush dispatch on simulation stop`

### Task 7: Market-aware feasible range and forced corrections (units)

**Files:**
- Modify: `assume/common/base.py` (`BaseUnit.calculate_position_correction` default), `assume/units/powerplant.py`, `assume/units/demand.py`
- Test: `tests/test_powerplant.py`, `tests/test_demand.py`

**Interfaces:**
- Consumes: Task 1 getters.
- Produces: `BaseUnit.calculate_position_correction(start, end, market_id) -> np.ndarray` (zeros by default); `PowerPlant`/`Demand` overrides per spec §5.5; `calculate_min_max_power(start, end, product_type="energy", market_id=None)` — with `market_id`, returns `(m - P_c, U - P_c)` per spec §5.4 (negative additional min allowed, no ≥0 clipping; `(0, 0)` when the unit cannot run); without, byte-identical to today. DSM overrides accept and ignore `market_id` (add the kwarg to their signatures).

- [ ] **Step 1: Write failing tests** (one per spec §5.5 row + Review Focus 2–4):

```python
def test_correction_zero_when_feasible()            # P0 within [m, U] -> c == 0
def test_correction_renewable_overcommitted()       # P0 300, U 220, m 0 -> c == -80
def test_correction_no_bid_when_partially_cleared() # P0 150, U 220 -> c == 0  (RF2)
def test_correction_whole_position_when_U_below_m() # P0 300, U 50, m 80 -> c == -300  (RF4)
def test_correction_between_zero_and_min()          # P0 20, m 40, U 100 -> c == +20; U 30 -> c == -20  (RF3)
def test_correction_demand()                        # P0 -950, demand_m -980 -> c == -30
def test_min_max_market_aware_negative_min()        # returns (m - P_c, U - P_c); without market_id unchanged
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the three overrides and the market-aware branch (reuse the existing heat/`capacity_pos`/`capacity_neg` terms for *m*ₜ and *U*ₜ; nearest-point-of-feasible-set logic for *c*).
- [ ] **Step 4: Run** `pytest tests/test_powerplant.py tests/test_demand.py tests/test_building.py -v` — PASS (DSM signature untouched behaviourally).
- [ ] **Step 5: Commit** `feat(units): forced position corrections and market-aware feasible ranges`

### Task 8: Correction bids in the strategy layer

**Files:**
- Modify: `assume/common/base.py` (`BaseStrategy`)
- Test: `tests/test_naive_strategies.py`

**Interfaces:**
- Consumes: Task 7 `calculate_position_correction`.
- Produces: `BaseStrategy.correction_price(unit, market_config, start, volume) -> float` (buy `volume < 0` → `market_config.maximum_bid_price`; sell → `minimum_bid_price`); `BaseStrategy.calculate_correction_bids(unit, market_config, product_tuples) -> Orderbook` (one simple bid per hour with `c != 0`, `volume=c`, standard fields; spec §6.1).

- [ ] **Step 1: Write failing tests:**

```python
def test_correction_bids_prices_and_signs():
    # c = -80 -> one bid volume -80 at maximum_bid_price
    # c = +30 -> one bid volume +30 at minimum_bid_price
    # c = 0 hours produce no bid

def test_correction_bid_at_cap_survives_market_validation():  # RF1
    # feed the bid through MarketRole.validate_orderbook with maximum_bid_price == bid price
    # -> price unchanged, no clipping warning
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** both methods on `BaseStrategy`.
- [ ] **Step 4: Run** `pytest tests/test_naive_strategies.py tests/test_market.py -v` — PASS.
- [ ] **Step 5: Commit** `feat(strategies): forced-correction bids with overridable correction price`

### Task 9: Naive strategy for positions; demand subclass; registry

**Files:**
- Modify: `assume/strategies/naive_strategies.py` (`EnergyNaiveStrategy`, new `DemandEnergyNaiveStrategy`), `assume/strategies/__init__.py`
- Test: `tests/test_naive_strategies.py`

**Interfaces:**
- Consumes: Tasks 7–8.
- Produces: `EnergyNaiveStrategy.calculate_bids` prepends `calculate_correction_bids`, passes `market_id=market_config.market_id` to `calculate_min_max_power`, and adds a buy bid (volume = negative additional min, price = marginal cost) when the economic min is below 0 (spec §6.2). `DemandEnergyNaiveStrategy(EnergyNaiveStrategy)` overrides only `correction_price`: buys at `unit.price.at[start]`, sells at `minimum_bid_price` (spec §6.3). Registry: `bidding_strategies["demand_energy_naive"] = DemandEnergyNaiveStrategy` (main dict, line ~65; leave `naive_eom`-style deprecated aliases on `EnergyNaiveStrategy`).

- [ ] **Step 1: Write failing tests:**

```python
def test_renewable_forced_buyback()        # position 300, ID avail 220 -> buy 80 at max price
def test_renewable_voluntary_curtailment() # position 200 feasible -> buy bid 200 at mc (0)
def test_renewable_sells_extra()           # ID avail above position -> sell bid for the extra at mc
def test_demand_single_market_unchanged()  # no position: exactly today's one bid (volume, price)
def test_demand_overbought_sells_at_floor()# position -980, forecast -950 -> sell +30 at minimum_bid_price
def test_demand_underbought_buys_at_wtp()  # position -950, forecast -980 -> buy -30 at unit price 3000
```

- [ ] **Step 2: Run** `pytest tests/test_naive_strategies.py -v` — FAIL.
- [ ] **Step 3: Implement** strategy changes, subclass, registry repoint.
- [ ] **Step 4: Run** `pytest tests/test_naive_strategies.py tests/test_demand.py -v` and the non-slow suite — PASS (single-market naive behaviour unchanged).
- [ ] **Step 5: Commit** `feat(strategies): position-aware naive bidding and demand sell-back`

### Task 10: Flexable fixes (overcommitment, cross-market profit)

**Files:**
- Modify: `assume/strategies/flexable.py` (`EnergyHeuristicFlexableStrategy.calculate_bids`, `.calculate_reward`)
- Test: `tests/test_flexable_strategies.py`

**Interfaces:**
- Produces: inflexible bid quantity clamped ≥ 0; flexible quantity = `max_power - max(inflex, 0)`; reward computes `profit` from accumulated `outputs["energy_cashflow"]` minus costs over the product hours (spec §7).

- [ ] **Step 1: Write failing tests:**

```python
def test_flexable_no_overcommitment_with_position():
    # position 300, min 100, max 500: bids sum (inflex+flex) <= 200 additional; no buy bid
def test_flexable_profit_accumulates_across_markets():
    # DA then ID feedback on same hour: outputs["profit"] reflects both cashflows
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** both fixes.
- [ ] **Step 4: Run** `pytest tests/test_flexable_strategies.py tests/test_advanced_order_strategy.py -v` — PASS (block/linked subclasses inherit the clamp; existing single-market assertions unchanged).
- [ ] **Step 5: Commit** `fix(strategies): flexable position clamp and cross-market profit`

### Task 11: complex_clearing child-surplus fix

**Files:**
- Modify: `assume/markets/clearing_algorithms/complex_clearing.py` (`calculate_order_surplus`)
- Test: `tests/test_complex_market_mechanisms.py`

**Interfaces:**
- Produces: child term uses `sum(child_order["volume"].values())` instead of the parent's `bid_volume` (spec §8.2).

- [ ] **Step 1: Write failing test:**

```python
def test_child_surplus_uses_child_volume():
    # parent BB (10 MW/h) at a loss of 5/MWh; child LB 90 MW at surplus 1/MWh:
    # child surplus 90 > parent loss 50 -> parent kept. With the bug (child volume
    # misread as 10), 10 < 50 -> parent wrongly removed. Assert both orders accepted.

def test_one_sided_hour_has_defined_price():   # spec §8.4
    # orderbook with sell orders only in one hour -> clear() returns a numeric price
    # for that hour and no unit volume between 0 and its all-or-nothing block
```

- [ ] **Step 2: Run** — FAIL (first test).
- [ ] **Step 3: Implement** the one-line fix.
- [ ] **Step 4: Run** `pytest tests/test_complex_market_mechanisms.py tests/test_clearing_edge_cases.py tests/test_clearing_paper_examples.py -v` — PASS; note any changed expectations in paper-example tests and verify them by hand before amending.
- [ ] **Step 5: Commit** `fix(markets): child linked-bid surplus computed with the child's own volume`

### Task 12: Thermal switch-price strategy

**Files:**
- Modify: `assume/strategies/advanced_orders.py` (new class + module helpers), `assume/strategies/__init__.py`
- Test: `tests/test_hourly_linked_strategy.py` (new)

**Interfaces:**
- Consumes: Task 7 ranges/corrections, Task 8 correction bids, `PowerPlant.calculate_cost(start, power) -> float` and `PowerPlant.calculate_chord_cost(start, p_from, p_to) -> float` — **defined in this task** on `PowerPlant` (`cost = power * calculate_marginal_cost(start, power)`; chord per spec §5.6, guard `p_to == p_from` → `calculate_marginal_cost(start, p_to)`), plus the `partial_load_eff` activation: `self.marginal_cost = None if partial_load_eff else self.calc_simple_marginal_cost()`, and curve selection by `technology` first, then `fuel_type` (spec §5.6).
- Produces: `EnergyHeuristicFlexableHourlyLinkedStrategy(EnergyHeuristicFlexableStrategy)`; registry `"powerplant_energy_heuristic_flexable_hourly_linked"`. Module helper `determine_on_case(cost_m: float, cost_U: float, m: float, U: float, R: float, shutdown_allowed: bool) -> tuple[str, dict[str, float]]` returning `("on-a"|"on-b"|"on-c", {"p_mU": ..., "p_0U": ..., "p_0m": ...})` with `p_mU=(cost_U-cost_m)/(U-m)`, `p_0U=(cost_U-R)/U`, `p_0m=(cost_m-R)/m`; on-c iff shutdown allowed and `p_0m < p_mU`, on-b iff shutdown allowed and `p_0m >= p_mU`.

Fixed decisions the implementer needs (spec §6.4 as amended):
- `R = unit.get_starting_costs(-unit.min_down_time)`.
- Shutdown allowed iff `unit.get_operation_time(start) >= unit.min_operating_time` **and** the next `min_down_time - 1` hours (within the index) have committed position 0.
- Per hour: correction bids first; recover `m = min_add + P_c`, `U = max_add + P_c` from `calculate_min_max_power(..., market_id=...)`; apply `calculate_ramp` to upward (sell) volumes only.
- Off (`P_c == 0`): **S** = single-hour BB, `volume={start: m}`, `min_acceptance_ratio: 1`, `bid_id=f"{unit.id}_S_{start:%Y%m%dT%H}"`, price = `calculate_marginal_cost(start, m)` adjusted via flexable's `calculate_EOM_price_if_on/off` by `op_time`; **F** = LB child of S, `volume={start: U-m}`, price `calculate_chord_cost(start, m, U)`. No bids while min down time unexpired.
- on-a: **I** sell SB `U-P_c` at `p_mU`; **D** buy single-hour BB `{start: P_c-m}` at `p_mU - 0.01`.
- on-b: **I** at `p_0U`; **X** buy single-hour BB `{start: P_c}`, `min_acceptance_ratio: 1`, at `p_0U - 0.01`; **no D**.
- on-c: **I** at `p_mU`; **D** at `p_mU - 0.01` (`bid_id=f"{unit.id}_D_{start:%Y%m%dT%H}"`); **X** = LB child of D, `{start: m}`, `min_acceptance_ratio: 1`, at `p_0m - 0.01`. If `P_c == m`: no D, X is a plain BB. If `P_c == U`: no I.
- Hour-to-hour chaining: `previous_power`/`op_time` advance along the committed position path `P_c` (do not assume own economic bids clear).
- Buy volumes are negative in the order dict; the table magnitudes above are absolute.

- [ ] **Step 1: Write failing tests for the cost helpers** (partial-load activation, technology-first curve selection, chord arithmetic — spec §5.6 values, e.g. CCGT eff 0.55, fuel 25, co2 25, ef 0.2 → avg cost at full load ≈ 54.5):

```python
def test_partial_load_eff_activates_curve()
def test_curve_selected_by_technology_then_fuel()
def test_chord_cost_matches_hand_value()
```

- [ ] **Step 2: Run** `pytest tests/test_powerplant.py -v` — FAIL; implement on `PowerPlant`; re-run — PASS.
- [ ] **Step 3: Write failing tests for `determine_on_case`** (pin with the spec's CCGT numbers: m=40, U=100, R=0 → `p_mU≈45.4`, `p_0U≈54.5`, `p_0m≈68.2`, case on-b; with R=2265 → on-c):

```python
def test_switch_prices_ccgt_r0_is_on_b()
def test_switch_prices_ccgt_hot_start_is_on_c()
def test_on_a_when_shutdown_not_allowed()
```

- [ ] **Step 4: Run** — FAIL; implement helper; re-run — PASS.
- [ ] **Step 5: Write failing tests for `calculate_bids`** covering: off case (S+F volumes, link, `min_acceptance_ratio`), each on sub-case's bid set, the two edge cases, no-self-cross (`max(buy prices) < min(sell prices)` per unit-hour), min-run/min-down suppression of X, on-b has no D, correction bid precedes economic bids, and first-index-hour bidding (RF5: product starting at `index[0]` raises nothing and `op_time` uses the look-back default).
- [ ] **Step 6: Run** — FAIL; implement the class; re-run — PASS.
- [ ] **Step 7: Clearing round-trip test:** build a 3-unit orderbook (thermal via the new strategy with a DA position, renewable buy-back, demand) and feed `ComplexClearingRole.clear`; assert no unit's net accepted position lands strictly between 0 and its min load.
- [ ] **Step 8: Run** `pytest tests/test_hourly_linked_strategy.py tests/test_powerplant.py -v` — PASS.
- [ ] **Step 9: Commit** `feat(strategies): hourly linked switch-price strategy for thermal units`

### Task 13: Example scenario `example_01k`

**Files:**
- Create: `examples/inputs/example_01k/generate_inputs.py`, generated CSVs + `config.yaml` in the same folder
- Modify: `REUSE.toml` (annotation for `examples/inputs/example_01k/**`), `examples/examples.py`
- Test: `tests/test_example_01k_generator.py` (new)

**Interfaces:**
- Consumes: spec §9 values verbatim (units table, market blocks, error config, correlation matrix, week 2019-03-17 → 2019-03-25, scaling 1000/76903 from example_03's full-year hourly `demand_EOM` peak).
- Produces: scenario folder loadable by `load_scenario_folder`; `examples.py` entry `"small_sequential_da_id": {"scenario": "example_01k", "study_case": "base"}`. Generator exposes `generate_forecast_errors(index: pd.DatetimeIndex, errors_cfg: dict, correlation: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame` and `main(out_dir: Path | None = None)`; config constants `SEED`, `SIM_START`, `SIM_END`, `PEAK_DEMAND_MW`, `SOURCE`, `ERRORS`, `CORRELATION` at the top (spec §9.3).

- [ ] **Step 1: Write failing generator tests** (import via `importlib.util.spec_from_file_location`):

```python
def test_generator_deterministic()        # two runs, same SEED -> identical frames
def test_error_sigma_and_correlation()    # generated innovations match ERRORS/CORRELATION within 20% over the week
def test_availability_clipped()           # DA availability within [0, 1]; solar zero where realised zero
def test_invalid_correlation_rejected()   # non-positive-definite matrix -> clear error
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement `generate_inputs.py`** per spec §9.3 steps 1–6 (AR(1) with Cholesky-scaled innovations; wind additive in availability, solar multiplicative, demand multiplicative; writes realised + `availability_DA_df.csv` + `demand_DA_df.csv`; writes the unit CSVs from spec §9.2 and `config.yaml` with the two market blocks from spec §8.1/§9.1 — copy those blocks exactly).
- [ ] **Step 4: Run** the generator (`python examples/inputs/example_01k/generate_inputs.py`), commit its outputs; add the `REUSE.toml` annotation and the `examples.py` entry.
- [ ] **Step 5: Run** `pytest tests/test_example_01k_generator.py -v` and `pre-commit run --all-files` — PASS (reuse lint accepts the annotation).
- [ ] **Step 6: Smoke-load:** `python -c "..."` calling `load_scenario_folder` on `example_01k`/`base` — no exceptions, both markets configured.
- [ ] **Step 7: Commit** `feat(examples): sequential DA/ID example with seeded forecast-error generator`

### Task 14: Integration test

**Files:**
- Create: `tests/test_sequential_integration.py`

**Interfaces:**
- Consumes: the committed `example_01k` inputs; everything above.

- [ ] **Step 1: Write the test** (`@pytest.mark.slow`): run `small_sequential_da_id` via `load_scenario_folder` + `world.run()` against a tmp sqlite DB, then assert (spec §9.4):

```python
def test_sequential_week_runs():
    # market_meta: DA and ID each cleared 7 times, 24 products each
    # no thermal unit's hourly position in (0, min_power) after ID
    # abs(unit_dispatch["energy_deviation"]).max() < 1e-6
    # per unit: sum_m energy_profit_m totals match energy_cashflow - operating cost (tolerance 1e-6)
    # battery soc within [0, 1]
    # unit_dispatch covers 2019-03-24 (on_stop flush, spec §5.1)
```

- [ ] **Step 2: Run** `pytest tests/test_sequential_integration.py -v` — PASS (fix forward anything it exposes; this is the system gate).
- [ ] **Step 3: Commit** `test: sequential DA/ID week integration test`

### Task 15: Regression check against main

**Files:** none (verification only; fixes belong to the task that caused them)

- [ ] **Step 1:** On this branch: `assume -s example_01a -c base -db sqlite:///./reg_branch_01a.db`, `assume -s example_01c -c dam_with_complex_opt_clearing -db sqlite:///./reg_branch_01c.db` (plus `example_01g` base for storage).
- [ ] **Step 2:** `git stash && git checkout main`, repeat into `reg_main_*.db`, `git checkout -` back.
- [ ] **Step 3:** Compare with pandas (`market_meta`, `market_dispatch`, `unit_dispatch` tables): identical except (a) new per-market/deviation columns, (b) `example_01c` complex-clearing case differences traceable to the Task 11 fix. Any other difference is a bug — fix in the owning task before proceeding.
- [ ] **Step 4:** Delete the `reg_*.db` files; record the confirmed differences for Task 16's release notes. Commit nothing.

### Task 16: Documentation and release notes

**Files:**
- Modify: `docs/source/release_notes.rst`, `docs/source/market_config.rst`, `docs/source/units.rst`
- Create: `examples/inputs/example_01k/README.md`

- [ ] **Step 1:** Release-notes entries (one line each): per-market forecasts; per-market accounting + `energy_deviation`; forced corrections + `correction_price`; new thermal strategy; `partial_load_eff` activation; flexable clamp + profit fix; demand sell-back fix; `complex_clearing` child-surplus fix (behaviour change, cite Task 15 findings); `on_stop` dispatch flush; new example `small_sequential_da_id`.
- [ ] **Step 2:** `market_config.rst`: a "Sequential markets" section — timing pattern (daily auctions, `first_delivery`, per-market `start_date`/`end_date`), per-market forecast file convention, position carry-over.
- [ ] **Step 3:** `units.rst` (and the bidding-strategy docs where the flexable strategies are described): per-market outputs, position vs physical output, corrections, the switch-price strategy with limitations L1–L2, lead-day cold start and the scarcity-reading note (spec §9.1–9.2, §11).
- [ ] **Step 4:** `README.md` in the example folder: what it shows, how to run, how to regenerate inputs and tune `ERRORS`/`CORRELATION`.
- [ ] **Step 5:** `pre-commit run --all-files` and `pytest -m "not slow and not require_learning and not require_network"` — all green.
- [ ] **Step 6:** Commit `docs: sequential markets documentation and release notes`

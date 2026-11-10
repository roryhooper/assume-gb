<!--
SPDX-FileCopyrightText: ASSUME Developers

SPDX-License-Identifier: MIT
-->

# Sequential day-ahead and intraday energy markets: design

- **Date:** 2026-11-10
- **Branch:** `feat/simplified_sequential_markets`
- **Status:** design approved in brainstorming, pending spec review

## 1. Purpose

Add an ASSUME example with two sequential, pay-as-clear, energy-only auctions for the same delivery day: a day-ahead (DA) auction and an intraday (ID) auction. Make the framework changes needed so that this works correctly for any unit type.

The key behaviour is that ID bidding reacts to two things: the position each unit already holds from DA, and that unit's updated forecast. For example, a gas unit that sold in DA above its cost buys back its position in ID if the ID price falls below what that position costs to run.

The design reuses existing ASSUME concepts (`market_id`-keyed forecasts, `outputs[...]` series, the strategy and unit registries, `complex_clearing`) and changes existing behaviour only where it is wrong once units hold positions from earlier markets.

### 1.1 Requirements (from the user)

- Both auctions trade the **same delivery day D**, and both are held on the day before (D−1):
  - **DA auction:** opens 09:00 on D−1; 24 × 1h products covering 00:00–23:00 of D; pay-as-clear.
  - **ID auction:** opens 22:00 on D−1; the same 24 × 1h products covering 00:00–23:00 of D; pay-as-clear.
  - ID therefore trades changes to the positions taken in DA for the same hours, two hours before delivery begins.
- **Run length:** one week of delivery.
- **No balancing or imbalance market in v1.** The design must allow one to be added later, in which agents may *choose* to be out of balance when that pays (see §10).
- **Any unit type** must be able to take part in this market setup.
- **Example units:**
  - wind onshore, wind offshore, solar;
  - three gas units with slightly different efficiencies and partial-load efficiency;
  - one hard coal unit and one lignite unit;
  - one battery;
  - one inflexible demand.
  - Capacities follow German 2019 ratios, with the demand peak at 1000 MW.
- **Forecasts per market:** renewable availability *and* demand forecasts differ between DA and ID.
- **Strategies:** reuse existing ones wherever possible.
- **Accounting:** cashflow and profit are tracked per market.

### 1.2 Decisions

| # | Decision |
|---|---|
| D1 | **Option A for physical truth:** the ID forecast equals the realised value. `forecaster.availability` and `forecaster.demand` remain the *realised* series. Separate per-market forecast series are added next to them. |
| D2 | Per-market forecasts exist for availability (all units) and demand (demand units). A unit or market without its own forecast falls back to the realised series. |
| D3 | The naive price and residual-load forecasts use each market's own availability and demand forecasts. |
| D4 | Per-market cashflow is recorded exactly. Profit per market uses sequential cost attribution, which adds up exactly to total profit. Demand's operating cost is zero. |
| D5 | **Forced corrections:** a position that can't be delivered under a market's forecast is offered back as price-taker bids at the market's price limits. The price comes from a single strategy method so that option B can override it later. |
| D6 | `partial_load_eff` is made functional. Thermal units bid through `complex_clearing` with `pay_as_clear` pricing in both markets. |
| D7 | New thermal strategy: an off unit bids an hourly all-or-nothing start block plus a linked flexible bid; a running unit bids one switch price per hour, found by comparing its profit at its extremes (§6.4). |
| D8 | **Approach:** generic building blocks in the framework, minimal fixes to existing strategies, and one new thermal strategy. |
| D9 | DA forecast errors come from a seeded generator with editable error sizes, persistence and cross-correlation. |
| D10 | **Example week:** Mon 18 – Sun 24 March 2019. Battery: 100 MW charge / 90 MW discharge, 190 MWh, 5 €/MWh charge and discharge cost. |
| D11 | No `.license` sidecar files. Licence information for generated example data goes in a `REUSE.toml` annotation. |

### 1.3 Out of scope (v1)

- Balancing market clearing and imbalance settlement. The hooks for them are part of this design (§10).
- A separate ID forecast that differs from realised values (option B data). The generator is structured to add it.
- Thermal shutdown blocks spanning several hours (§6.4, limitation L1).
- Per-market forecasts for DSM units. They keep using their existing forecasts and accept the new `market_id` argument without using it.
- A results notebook.

## 2. Existing behaviour this design builds on or corrects

Established by reading the code at `0a3724d7`:

| Area | Current behaviour | Consequence |
|---|---|---|
| Multiple energy markets | `BaseUnit.set_dispatch_plan` adds each market's accepted volume into `outputs["energy"]`. | The net position across markets is already tracked (good). |
| Feasible range | `PowerPlant`/`Demand.calculate_min_max_power` and the storage charge/discharge equivalents are relative to the committed `outputs["energy"]`. | ID bids already see the DA position (good). |
| Availability | `UnitForecaster.availability` is one series, used both for bidding and as the physical limit in `PowerPlant.execute_current_dispatch`. | There's no way to have different forecasts per market. |
| Prices | `forecaster.price` is a dict keyed by `market_id`. | This is the pattern to copy. |
| Reducing a position | `PowerPlant.calculate_min_max_power` clips the additional max at ≥ 0. | Renewables can't buy back a shortfall. |
| Flexable with a position | A negative additional min becomes a buy at the inflexible-bid price, plus a sell of `max − (negative min)`. | It can commit beyond capacity (e.g. 300 MW position → 200 MW buy and 400 MW sell → 700 MW), which is then silently clipped. |
| Flexable reward | `calculate_reward` overwrites `outputs["profit"]` on each market's feedback. | DA revenue is lost after the ID feedback. |
| `energy_accepted_price` | Overwritten by each market. | Only the last market's price is kept (per-market prices remain in the market results tables). |
| Partial-load efficiency | `PowerPlant.__init__` always precomputes `marginal_cost`, so `calc_marginal_cost_with_partial_eff` is never reached. The curve is selected by `fuel_type` but tested against technology names. | `partial_load_eff` does nothing. |
| Demand over-bought | `demand_energy_naive` bids (forecast − position) at its 3000 €/MWh bid price; a negative remainder becomes a sell at 3000. | An over-bought demand can never sell back. |
| Demand cost | `calculate_generation_cost` charges demand |*P*| × its bid price. | `energy_generation_costs` for demand is meaningless. |
| `complex_clearing` child surplus | `calculate_order_surplus` computes a child bid's surplus using the **parent's** volume. | Linked children are valued wrongly when their volume differs from the parent's. |
| Start-up thresholds | `PowerPlant` defaults `downtime_hot_start` and `downtime_warm_start` to 0, although the docstring says 8 and 48. | Every start is a cold start unless these are set explicitly. |
| Storage reward | Uses the accumulated `energy_cashflow`. | Already correct across markets. |

## 3. Architecture overview

```
Forecaster layer      per-market availability/demand forecasts (+ fallback to realised)
        │
Unit layer            feasible range per market, forced correction, position vs physical,
        │             per-market volume/cashflow/cost/profit, partial-load costs
        │
Strategy layer        correction bids + correction_price hook (BaseStrategy)
        │             naive (renewables), DemandEnergyNaive, flexable storage (unchanged),
        │             flexable (bug fixes), NEW flexable_hourly_linked (thermal)
        │
Market layer          complex_clearing, pay_as_clear pricing (child surplus fix)
```

Single-market behaviour is unchanged. When a unit holds no earlier position and no per-market forecast exists, every new code path reduces to today's behaviour. The only intended changes to existing results are the bug fixes in §5.3, §6.3 and §7.2.

## 4. Forecasts and data loading

### 4.1 Forecaster API (`assume/common/forecaster.py`)

- `UnitForecaster`:
  - new constructor argument and attribute `availability_forecast: dict[str, FastSeries]` (default empty);
  - new method `get_availability(market_id: str | None = None) -> FastSeries`, which returns `availability_forecast[market_id]` if present, else `availability`;
  - each forecast series is validated within [0, 1], as `availability` is today.
- `DemandForecaster`:
  - new `demand_forecast: dict[str, FastSeries]`;
  - new `get_demand(market_id=None)` with the same fallback;
  - validated ≤ 0.
- `availability` and `demand` keep their meaning as the realised series.

### 4.2 Loader (`assume/scenario/loader_csv.py`)

- For each market id in `markets_config`, the loader optionally reads `availability_<market_id>_df.csv` and `demand_<market_id>_df.csv`.
- **Layout:** identical to `availability_df.csv` / `demand_df.csv`, with one column per unit.
- **Processing:** the same normalisation (availability > 1) and sign handling (demand → −|x|) as the realised files.
- **Fallback:** a missing file or unit column means no forecast entry, so the realised series is used.
- The per-market series are passed into the forecaster constructors via the new dict arguments.

### 4.3 Forecast algorithms (`assume/common/forecast_algorithms.py`)

- `calculate_max_power` and `calculate_sum_demand` take a `market_id` argument and use the getters. It defaults to `None` (the realised series), so their other callers — the congestion signal and renewable-utilisation forecasts — are unaffected.
- `calculate_naive_price_inelastic`, `calculate_naive_price_elastic` and `calculate_naive_residual_load` pass `config.market_id`.
- **Result:** the DA and ID price forecasts differ by exactly the forecast revision.
- **Why this is a reasonable proxy:** when bids reflect cost, the ID auction re-dispatches towards the merit order of the updated residual load. The merit-order price at that load therefore approximates the ID price, apart from effects of DA commitments such as start-ups already paid for and storage state of charge.

### 4.4 Tests

- Getter fallback and validation.
- The loader reads per-market files and falls back when a file or column is missing.
- A two-market naive price forecast differs when the inputs differ.
- A single-market example's forecasts are identical to today's.

## 5. Units: positions, accounting, corrections, partial-load costs

### 5.1 Position vs physical output (`assume/common/base.py`, `assume/common/units_operator.py`)

- `outputs["energy"]` keeps its meaning: before delivery it holds the net position across markets; after `execute_current_dispatch` it holds physical output.
- `BaseUnit.set_dispatch_plan` also adds each order's accepted volume to `outputs[f"{product_type}_{market_id}"]`. Orders carry `market_id`, set in `UnitsOperator.handle_market_feedback`.
- `UnitsOperator.get_actual_dispatch`:
  - copies the position for the hours being executed before calling `execute_current_dispatch`;
  - then writes `outputs["energy_deviation"]` = physical output − position.
- **End-of-simulation flush:** `write_actual_dispatch` currently runs only from `handle_market_feedback`. With daily markets, the last feedback (ID at 23:00 on D−1) comes *before* the final delivery day, so that day's dispatch would never be executed or exported. `UnitsOperator` gains an `on_stop` that calls `write_actual_dispatch` for each traded product type, mirroring the output agent's existing `on_stop` flush.

### 5.2 Per-market cashflow

- `BaseUnit.calculate_cashflow` also adds to `outputs[f"{product_type}_cashflow_{market_id}"]`.
- The total `energy_cashflow` is unchanged.

### 5.3 Per-market cost and profit by sequential attribution

- **New method:** `calculate_operating_cost(start, end) -> np.ndarray`, the hourly cost of the current `outputs["energy"]`.
  - `BaseUnit` default: |*P*ₜ| × `calculate_marginal_cost(t, Pₜ)`. With partial-load efficiency this is correct, because that method returns the *average* cost at *P*.
  - `SupportsMinMax`: adds start-up costs (`get_starting_costs`) for off→on transitions.
  - `Demand`: returns zeros. An inflexible demand has no operating cost; its economic outcome is its payments, which are already in its cashflow. Its `price` is a willingness to pay used for bidding, not a cost it incurs.
- **In `set_dispatch_plan`:**
  - evaluate `calculate_operating_cost` from the first affected hour to the end of the unit index, before and after applying the market's volumes. The full-horizon window is needed for exactness: adding volume in one hour can split a long off-period and reclassify a *later* start from warm/cold to hot, outside any short window. Over a one-week hourly index the extra evaluation cost is negligible;
  - store the difference (which is zero outside the hours whose cost actually changed) in `outputs[f"{product_type}_total_costs_{market_id}"]`.
- **After the cashflow is computed:** `outputs[f"{product_type}_profit_{market_id}"]` = `energy_cashflow_<m>` − `energy_total_costs_<m>`.
- **Property:** Σₘ profitₘ = total cashflow − cost of the final position.
- **Export:** `get_actual_dispatch` adds `"energy_profit"` and `"energy_deviation"` to its `valid_outputs` substring filter. `energy_cashflow_<m>` and `energy_total_costs_<m>` already match the existing `"cashflow"` and `"total_costs"` filters. The existing strategy-level `profit` output is not exported, as today.

### 5.4 Feasible range per market

- Signature: `calculate_min_max_power(start, end, product_type="energy", market_id=None)`. Overrides elsewhere (e.g. DSM units) accept and ignore the new argument.
- `PowerPlant` uses `forecaster.get_availability(market_id)`; `Demand` uses `forecaster.get_demand(market_id)`.
- **With a `market_id`:** the returned range is the *economic* range relative to the **corrected** position *P*꜀ = *P*₀ + *c* (§5.5): for `PowerPlant`, [*m*ₜ − *P*꜀, *U*ₜ − *P*꜀] (zeros if the unit cannot run). The additional minimum **may be negative** — that is the unit's capacity to reduce a committed position — so the existing ≥ 0 clipping of the additional min and max does **not** apply on this path. Correction bids and economic bids never overlap, because both are measured from *P*꜀.
- **Without one:** behaviour is unchanged, including the ≥ 0 clipping.
- Storage charge and discharge ranges are unchanged.

### 5.5 Forced corrections

- **New method:** `calculate_position_correction(start, end, market_id) -> np.ndarray`.
  - It returns *c* per hour: the distance from the position *P*₀ to the nearest point of the feasible set *F*ₘ for this market.
  - *c* < 0 means the unit must reduce its net output, i.e. buy; *c* > 0 means it must increase, i.e. sell.
  - The default (`BaseUnit`, storage, other units) is zeros.

| Unit | *F*ₘ |
|---|---|
| `PowerPlant` | {0} ∪ [*m*ₜ, *U*ₜ], with *m*ₜ = `min_power` + `capacity_neg`, raised to any heat demand not yet covered, and *U*ₜ = availabilityₘ × `max_power` − `capacity_pos`. If *U*ₜ < *m*ₜ, then *F*ₘ = {0}. A position strictly between 0 and *m*ₜ moves to *m*ₜ if *m*ₜ ≤ *U*ₜ, otherwise to 0. |
| `Demand` | the single point demandₘ(*t*) |

- Ramp limits are not applied to corrections; they apply to economic bids only.

### 5.6 Partial-load efficiency (`assume/units/powerplant.py`)

- If `partial_load_eff` is true, `self.marginal_cost = None`, so `calculate_marginal_cost` uses `calc_marginal_cost_with_partial_eff`.
- `calc_marginal_cost_with_partial_eff` selects the efficiency-loss curve by `technology` first (`"combined cycle gas turbine"`, `"open cycle gas turbine"`, `"hard coal"`, `"lignite"`), then by `fuel_type`. This keeps today's fuel-type matching for coal and lignite.
- **New helpers:**
  - `calculate_cost(start, power)` = power × `calculate_marginal_cost(start, power)`;
  - `calculate_chord_cost(start, p_from, p_to)` = (cost(*p*_to) − cost(*p*_from)) / (*p*_to − *p*_from).
- No existing example sets `partial_load_eff`, so their behaviour is unchanged.

### 5.7 Tests

- Per-market volumes add up to `outputs["energy"]`, and per-market cashflows add up to `energy_cashflow`.
- Attributed profits add up to total profit, including a case where an ID buy-back avoids a start-up.
- Demand's operating cost is zero.
- `energy_deviation` is correct when a correction doesn't clear.
- Correction values for every row of the §5.5 table.
- The economic range doesn't overlap the correction.
- Partial-load cost matches the curve, and the curve is selected by technology.
- The unit dispatch export for an existing single-market example matches today's apart from the new columns.
- The `on_stop` flush exports the final delivery day's dispatch (a daily-market run without it would lose that day).

## 6. Strategies

### 6.1 Shared: correction bids (`BaseStrategy`)

- `calculate_correction_bids(unit, market_config, product_tuples) -> Orderbook`:
  - one simple bid per hour where *c* ≠ 0, with `volume = c`, allowed to clear partially;
  - price from `correction_price`.
- `correction_price(unit, market_config, start, volume) -> float`: the single override point.
  - **Default (option A):** `market_config.maximum_bid_price` for buys (*c* < 0) and `market_config.minimum_bid_price` for sells (*c* > 0).
- Strategies opt in by prepending these bids to their orderbook.

### 6.2 Renewables: `powerplant_energy_naive` (`EnergyNaiveStrategy`)

1. Correction bids.
2. **Sell more:** sell the economic additional max at marginal cost, as today.
3. **New, voluntary curtailment:** if the economic additional min is below 0, buy that amount at marginal cost. A renewable unit therefore curtails only when the price is below its marginal cost (typically negative prices).
4. Pass `market_config.market_id` to `calculate_min_max_power`.

In a single market the position is 0, so there's no buy bid and the result is identical to today. This also applies to existing thermal units that use this strategy.

### 6.3 Demand: `demand_energy_naive`

- **New subclass:** `DemandEnergyNaiveStrategy(EnergyNaiveStrategy)` replaces the registry target of `demand_energy_naive`. Config names are unchanged.
- **Pricing:** it overrides `correction_price` to price buys at the unit's `price` (willingness to pay); sells keep the default `minimum_bid_price`.
- **What it bids:** for inflexible demand the correction is the whole remaining demand, so it bids only correction bids. In a single market this reproduces today's bid exactly.
- **Bug fixed:** an over-bought demand now sells back at `minimum_bid_price`.

### 6.4 Thermal: new `powerplant_energy_heuristic_flexable_hourly_linked`

**Class.** `EnergyHeuristicFlexableHourlyLinkedStrategy(EnergyHeuristicFlexableStrategy)` in `assume/strategies/advanced_orders.py`, registered in `bidding_strategies`. It reuses flexable's helpers:
- `calculate_EOM_price_if_off` (start-up markup);
- `calculate_EOM_price_if_on` (discount for staying on);
- `calculate_ramp`;
- the way the strategy assumes the previous hour's bids are accepted when carrying running and off times forward;
- `calculate_reward` (with the §7.2 fix).

**Terms.**

| Symbol | Meaning |
|---|---|
| *P*꜀ | corrected position for the hour (net of earlier markets, after §5.5) |
| *m* | min load (as *m*ₜ in §5.5) |
| *U* | available max for this market (as *U*ₜ in §5.5), limited by ramp rates |
| cost(*P*) | `calculate_cost(t, P)` |
| *R* | restart penalty = `get_starting_costs(-min_down_time)`, the cost of restarting after the minimum down time |
| SB / BB / LB | simple / block / linked bid types in `complex_clearing` |

| Bid | Stands for | Direction | Volume | Clearing rule |
|---|---|---|---|---|
| **C** | Correction | buy or sell | *c* (§5.5) | SB, price from `correction_price` |
| **S** | Start | sell | *m* | single-hour BB, `min_acceptance_ratio: 1` |
| **F** | Flexible | sell | *U* − *m* | LB, `parent_bid_id` = S |
| **I** | Increase | sell | *U* − *P*꜀ | SB |
| **D** | Decrease | buy | *P*꜀ − *m* | single-hour BB, can partly clear |
| **X** | eXit (shut down) | buy | *m* | LB, `parent_bid_id` = D (BB without a parent if D has zero volume), `min_acceptance_ratio: 1` |

**Bid IDs.** Parent bids get explicit, unique ids: `f"{unit.id}_{kind}_{start:%Y%m%dT%H}"` with `kind` ∈ {S, D}.

**Per hour:**

1. **Correction:** bid **C** if *c* ≠ 0.
2. **Off** (*P*꜀ = 0):
   - If the minimum down time isn't over yet, the unit can't start and bids nothing.
   - Otherwise it bids **S**, priced at cost(*m*)/*m* plus flexable's start-up markup if the previous hour is off, or minus its stay-on discount if it's planned on.
   - It also bids **F**, priced at `calculate_chord_cost(m, U)`.
3. **On** (*P*꜀ ≥ *m*): one switch decision for the hour.
   - **Allowed extremes:** {0 if shutdown is allowed, *m*, *U*}.
   - **Shutdown is allowed** when both hold:
     - the unit has been running at least `min_operating_time` hours at this hour, counting committed positions;
     - the next `min_down_time − 1` hours have position 0, so shutting down here creates no off-period shorter than `min_down_time`.
   - **Profit at each extreme** is a straight line in the market price *p*: π₀ = −*R*, π*m* = *p*·*m* − cost(*m*), π*U* = *p*·*U* − cost(*U*).
   - **Switch prices** are where the best of these lines changes (taking the highest line at each price). In general:

| Sub-case | Condition | Bids |
|---|---|---|
| on-a | shutdown not allowed | **I** at *p*mU = `calculate_chord_cost(m, U)`; **D** at *p*mU − 0.01 |
| on-b | shutdown allowed and *p*₀m ≥ *p*mU (min load never best) | **I** at *p*₀U = (cost(*U*) − *R*)/*U*; **D** and **X** at *p*₀U − 0.01 |
| on-c | shutdown allowed and *p*₀m < *p*mU, with *p*₀m = (cost(*m*) − *R*)/*m* | **I** at *p*mU; **D** at *p*mU − 0.01; **X** at *p*₀m − 0.01 |

   - **Edge cases:** if *P*꜀ = *U* there's no **I**; if *P*꜀ = *m* there's no **D** and **X** has no parent.

**Why one switch price.** Under partial-load efficiency, the average cost falls with output (cost(*P*) is concave), so profit for the hour is always highest at an extreme. Pricing a reduction and an increase at their own average costs would put the unit's buy price *above* its sell price. In the CCGT example, with *P*꜀ = 70, *m* = 40 and *U* = 100, D would be 47.1 and I 43.7 €/MWh. The welfare-maximising clearing would then match the unit with itself, leaving it at its worst option, possibly between 0 and min load, and adding fake welfare.

The switch prices increase with each step up in output, and every buy is set 0.01 below the corresponding sell, so a unit's buys are always below its sells. Bids that clear fully are profitable:
- every MWh sold above *P*꜀ costs less than the switch price;
- every MWh bought back saves more than it.

**In sequence:**
- **DA:** every hour is "off", so the unit bids S + F. `complex_clearing` judges S together with its F bid (a child's surplus can justify its parent; this needs the §8.2 fix), so S + F behave like a single "start and run" offer.
- **ID:** committed hours use the on cases. Example (on-b, CCGT 70 MW position, *R* small): an ID price of 45 is below *p*₀U ≈ 54.5, so D + X clear and the unit buys back all 70 MW, gaining ≈ +993 € against staying on. At 56, I clears and the unit ramps to *U*.

### 6.5 Storage: `storage_energy_heuristic_flexable`

- No code change:
  - its ranges are already relative to the position, so it can reverse in ID;
  - it reads `forecaster.price[market_id]`, which now differs per market (§4.3);
  - its reward uses the accumulated `energy_cashflow`.
- No correction bids: its positions are kept feasible for state of charge when its bids are built.

### 6.6 Tests

- **Renewables:** forced buy, voluntary curtailment at a negative price, selling extra output.
- **Demand:** buying more at its willingness to pay, selling back at `minimum_bid_price`, and an identical single-market bid.
- **Storage:** reversing a DA discharge in ID.
- **Thermal, per case (off, on-a, on-b, on-c, edge cases):**
  - volumes, prices and links;
  - every buy price is below every sell price for each unit-hour;
  - a buy-back clears when the ID price is below the switch price;
  - X is suppressed when the minimum running or down time would be violated.
- **Small `complex_clearing` run:** no unit ends between 0 and min load.

## 7. Fixes to the existing flexable strategy

### 7.1 No overcommitment

In `EnergyHeuristicFlexableStrategy.calculate_bids`:
- clamp the inflexible bid quantity at ≥ 0;
- compute the flexible quantity as `max_power − max(inflex, 0)`.

With a prior position, the strategy then never commits beyond capacity, and it doesn't offer reductions (that is the new strategy's job). Single-market results are identical.

### 7.2 Profit across markets

`EnergyHeuristicFlexableStrategy.calculate_reward` sets `profit` = accumulated `energy_cashflow` − total costs over the product hours, as the storage strategy does, instead of revenue from the current orderbook only.

### 7.3 Tests

- No overcommitment with a DA position.
- Profit is correct after two markets.

## 8. Market clearing

### 8.1 Configuration (both markets)

```yaml
market_mechanism: complex_clearing
param_dict:
  solver_name: highs
  pricing_mechanism: pay_as_clear
additional_fields: [bid_type, min_acceptance_ratio, parent_bid_id]
volume_unit: MWh
price_unit: EUR/MWh
maximum_bid_volume: 100000
maximum_bid_price: 3000
minimum_bid_price: -500
```

Strategies that don't set the extra fields get `None` (via `BaseUnit.calculate_bids`), which `complex_clearing` treats as SB.

### 8.2 Bug fix: child surplus

In `calculate_order_surplus` (`assume/markets/clearing_algorithms/complex_clearing.py`), use the child's own summed volume, `sum(child_order["volume"].values())`, instead of the parent's `bid_volume`.
- This intentionally changes results of the existing `small_with_BB_and_LB` example.
- Recorded in the release notes.

### 8.3 Pricing behaviour

- Prices are the LP duals once the block decisions are fixed. Blocks accepted at a loss are removed one by one and the problem is re-solved (existing behaviour).
- **ID hours with little or no trading:** the ID auction trades only changes, so some hours have few or no accepted orders and the dual price there is poorly defined. A test pins down the reported value. The example's analysis guidance says to flag hours with no trades rather than read their price literally.

### 8.4 Tests

- Child surplus uses the child's own volume.
- An ID hour with orders on one side only yields a defined price, and no unit is left between 0 and min load.

## 9. Example scenario `example_01k`

### 9.1 Registration and timing

- `examples/examples.py`: `"small_sequential_da_id": {"scenario": "example_01k", "study_case": "base"}`.
- `config.yaml`, study case `base`:
  - `start_date: 2019-03-17 00:00`, `end_date: 2019-03-25 00:00`, `time_step: 1h`.
  - 17 March is a lead day with no deliveries; 18–24 March is the delivered week; analysis excludes 17 March.
  - Because the lead day has zero output, every thermal unit reads as long-off at the first DA auction and prices its first start accordingly (a cold start under `get_operation_time`'s look-back). This is intended; the first delivery day carries start-up effects.

| | DA | ID |
|---|---|---|
| `operator` | `DA_operator` | `ID_operator` |
| `product_type` | `energy` | `energy` |
| `start_date` | `2019-03-17 09:00` | `2019-03-17 22:00` |
| `end_date` | `2019-03-23 10:00` | `2019-03-23 23:00` |
| `opening_frequency` / `opening_duration` | `24h` / `1h` (clears 10:00) | `24h` / `1h` (clears 23:00) |
| `products` | `{duration: 1h, count: 24, first_delivery: 15h}` | `{duration: 1h, count: 24, first_delivery: 2h}` |

The per-market `end_date`s stop either market from opening for 25 March, which is beyond the unit index.

### 9.2 Units

- **Scaling factor:** *k* = 1000 / 76,903, so the 2019 hourly peak of example_03's `demand_EOM` becomes 1000 MW.
- **Capacities:** example_03's German 2019 capacities × *k*.
- **Thermal parameters:** capacity-weighted averages per technology from example_03's `powerplant_units.csv`.

| Unit | Technology / fuel | MW | Efficiency | `min_power` | Ramp up/down (MW/h) | Additional cost | Hot/warm/cold start (€/MW) | Min run / down time (h) | Emission factor |
|---|---|---|---|---|---|---|---|---|---|
| Wind Onshore | wind_onshore / renewable | 692 | 1 | 0 | – | 0 | 0 | 0 / 0 | 0 |
| Wind Offshore | wind_offshore / renewable | 98 | 1 | 0 | – | 0 | 0 | 0 / 0 | 0 |
| Solar | solar / renewable | 635 | 1 | 0 | – | 0 | 0 | 0 / 0 | 0 |
| Gas 1 | combined cycle gas turbine / natural gas | 94 | 0.58 | 0.41 × MW | 0.84 × MW | 3.5 | 24.1 / 34.2 / 46.7 | 5 / 3 | 0.201 |
| Gas 2 | combined cycle gas turbine / natural gas | 94 | 0.55 | 0.41 × MW | 0.84 × MW | 3.5 | 24.1 / 34.2 / 46.7 | 5 / 3 | 0.201 |
| Gas 3 | combined cycle gas turbine / natural gas | 93 | 0.52 | 0.41 × MW | 0.84 × MW | 3.5 | 24.1 / 34.2 / 46.7 | 5 / 3 | 0.201 |
| Hard coal | hard coal / hard coal | 321 | 0.41 | 0.41 × MW | 0.69 × MW | 1.3 | 32.7 / 51.6 / 69.9 | 7 / 6 | 0.335 |
| Lignite | lignite / lignite | 271 | 0.39 | 0.50 × MW | 0.53 × MW | 1.65 | 32.5 / 51.3 / 69.9 | 10 / 7 | 0.406 |

- **Gas units:** the three gas units carry Germany's total gas capacity (CCGT + OCGT, 281 MW), all modelled as CCGTs with slightly different efficiencies.
- **Thermal unit settings:** all thermal units have `partial_load_eff: true`, `downtime_hot_start: 8` and `downtime_warm_start: 48`.
- **Strategies:**
  - renewables use `powerplant_energy_naive` in both markets;
  - thermal units use `powerplant_energy_heuristic_flexable_hourly_linked` in both markets.
- **Battery** (`storage_units.csv`):
  - `max_power_charge` 100, `max_power_discharge` 90, `capacity` 190;
  - `efficiency_charge` / `efficiency_discharge` 0.95;
  - `additional_cost_charge` / `additional_cost_discharge` 5;
  - `min_soc` 0, `max_soc` 1, `initial_soc` 0.5;
  - technology `li-ion_battery`, `storage_energy_heuristic_flexable` in both markets.
  - Power and energy approximate total German storage (pumped hydro + batteries) × *k*, with example_01g's battery duration and efficiencies. example_01g's 30 €/MWh cost uses `variable_cost_*` column names that `Storage` doesn't read, so 5 €/MWh (a plausible degradation cost) is used instead.
- **Demand** (`demand_units.csv`): one unit, `max_power` −1000, `min_power` 0, `price` 3000, `demand_energy_naive` in both markets.
- **Operators:** one per group: `Operator-RE`, `Operator-Gas`, `Operator-Coal`, `Operator-Storage`, `Operator-Demand`.
- **Fuel prices:** example_03's `fuel_prices_df`, hourly mean, for the simulated window.
- **Adequacy for the week:** dispatchable capacity is 873 MW, and residual load ranges from 167 to 874 MW. The one tight hour is covered by the battery.
- **Reading scarcity prices:** a DA over-forecast of demand (or under-forecast of wind/solar) can push a DA hour to the 3000 cap even though realised capacity suffices, with ID correcting back down. This is an intended effect of forecast errors; the example docs say to check the ID price and the forecast error before reading a cap event as physical scarcity.

### 9.3 Generator `examples/inputs/example_01k/generate_inputs.py`

**Config block at the top:**
- `SEED`, `SIM_START = "2019-03-17"`, `SIM_END = "2019-03-25"`, `PEAK_DEMAND_MW = 1000`, `SOURCE = "example_03"`.
- `ERRORS["DA"]`, per series:

| Series | Error size σ | Persistence φ |
|---|---|---|
| `wind_onshore` | 0.06 (fraction of capacity) | 0.9 |
| `wind_offshore` | 0.06 | 0.9 |
| `solar` | 0.04 (relative to realised output) | 0.8 |
| `demand` | 0.015 (relative) | 0.9 |

- `CORRELATION["DA"]`, correlation of the error innovations:

| | onshore | offshore | solar | demand |
|---|---|---|---|---|
| onshore | 1 | 0.6 | −0.1 | 0.1 |
| offshore | 0.6 | 1 | −0.1 | 0.1 |
| solar | −0.1 | −0.1 | 1 | −0.3 |
| demand | 0.1 | 0.1 | −0.3 | 1 |

The defaults are assumptions based on physical reasoning, not calibrated values:
- onshore and offshore share weather systems;
- wind and solar are only weakly related through fronts;
- solar on rooftops and local grids lowers net demand;
- wind chill raises heating load.

They are documented as assumptions in the script.

**Process:**
1. Read example_03's `demand_df` (`demand_EOM`), `availability_df` (Wind Onshore, Wind Offshore, Solar) and `fuel_prices_df`. Resample 15-min data to hourly means. Scale demand by *k* (computed from the full-year hourly peak) and select the simulated window.
2. Write the realised `demand_df.csv`, `availability_df.csv` and `fuel_prices_df.csv`, and the unit CSVs from §9.2 (capacities computed from the example_03 totals × *k*).
3. **For each market in `ERRORS`:**
   - simulate *e*ₜ = Φ*e*ₜ₋₁ + *L*εₜ;
   - *L* is the Cholesky factor of the correlation matrix, scaled so each series has its σ;
   - εₜ ~ N(0, I) from `numpy.random.default_rng(SEED)`.
4. **Apply the errors:**
   - wind: availability + *e*, clipped to [0, 1];
   - solar: availability × (1 + *e*), clipped to [0, 1], so it's zero at night;
   - demand: demand × (1 + *e*).
5. Write `availability_<market>_df.csv` and `demand_<market>_df.csv`. In v1 only `DA` is defined, so ID falls back to realised (option A). Under option B, an `ID` entry is added and the script produces its files unchanged otherwise.
6. **Validation:** the script validates the correlation matrix (symmetric, positive definite) and every σ and φ, and stops with a clear error message if any is invalid.

**Licensing:** add an annotation to `REUSE.toml` for `examples/inputs/example_01k/**` (`SPDX-FileCopyrightText: ASSUME Developers`, `SPDX-License-Identifier: MIT`). The generator script and `config.yaml` carry their own SPDX headers. No `.license` files.

### 9.4 Tests

- **Generator:** deterministic for a given seed; generated error sizes and correlations match the config within a tolerance; availability stays within [0, 1].
- **Integration (`@pytest.mark.slow`):** a full run of `small_sequential_da_id` checks that:
  - both markets clear every day;
  - no thermal unit ends between 0 and min load;
  - |`energy_deviation`| ≈ 0;
  - per-market profits add up to total profit;
  - the battery's state of charge stays within bounds.

## 10. Path to option B (future balancing market)

The v1 design deliberately leaves these extension points:

1. **Realised vs forecast:** `availability` and `demand` are realised; per-market forecasts are separate. Option B adds `availability_ID_df.csv` / `demand_ID_df.csv` (the generator's `ERRORS["ID"]`), so the ID forecast differs from realised.
2. **Deviation:** `energy_deviation` (physical − position) is already computed and exported. A balancing settlement prices this series.
3. **Voluntary imbalance:** a strategy overrides only `correction_price`, e.g. returning the expected imbalance price from `forecaster.price["BAL"]`. The unit then corrects in ID only when that is cheaper than staying out of balance.
4. **Balancing market:** it is another entry in `markets_config` with its own `market_id`, forecasts and clearing mechanism. Nothing in v1 assumes exactly two markets; all per-market logic is keyed by `market_id` and processed in clearing order.
5. **Commitment-aware ID price forecast (addresses L3):** the forecaster's existing `update` lifecycle hook is the natural place to revise the ID price forecast after the DA result is known (e.g. from DA-committed positions and start-ups already paid for), without changing the forecaster interface again.

## 11. Known limitations

- **L1:** thermal decisions are made one hour at a time. Effects across hours enter only through *R* and the shutdown-allowed check. A unit committed for a long DA run can only shut down in ID at the end of the run (where later hours are off), although it can always reduce to min load (D). Shutdown blocks over several hours are a later refinement.
- **L2:** a bid that clears only partly at exactly its switch price can lose a little, bounded by the difference between the incremental cost and the switch price over that partial volume.
- **L3:** the naive ID price forecast ignores DA commitment effects (start-ups already paid for, storage state of charge).
- **L4:** the forecast error correlations are uncalibrated assumptions.
- **L5:** the battery has no per-market forecast and no correction bids; its feasibility relies on state-of-charge-aware bid construction.

## 12. Backward compatibility and regression check

- **Run** examples `small`, `small_with_vre_and_storage` and `small_with_BB_and_LB` on `main` and on the branch, and compare `market_meta`, `market_dispatch` and `unit_dispatch`.
- **Expected:**
  - identical results, except new columns (`energy_cashflow_<m>`, `energy_total_costs_<m>`, `energy_profit_<m>`, `energy_deviation`);
  - `small_with_BB_and_LB` changes because of the §8.2 fix.
- `calculate_generation_cost` (and so demand's `energy_generation_costs`) is deliberately left unchanged in v1.
- Each difference is listed in the release notes.

## 13. Documentation

- **`docs/source/release_notes.rst`:**
  - per-market forecasts;
  - per-market accounting and deviation outputs;
  - forced corrections and the `correction_price` hook;
  - the new thermal strategy;
  - the partial-load fix;
  - the flexable overcommitment and profit fixes;
  - the demand sell-back fix;
  - the `complex_clearing` child-surplus fix;
  - the new example.
- **`docs/source/market_config.rst`:** a section on sequential markets (timing, per-market forecast files, how positions carry over).
- **Unit and forecaster docs** (`units.rst` and the forecaster page): per-market forecasts, position vs physical output, per-market outputs, forced corrections, partial-load efficiency.
- **Bidding strategy docs:** the new thermal strategy, including the switch-price rule and limitations L1–L2.
- **The example list:** `example_01k`, including how to regenerate inputs and adjust forecast errors.

<!--
SPDX-FileCopyrightText: ASSUME Developers

SPDX-License-Identifier: MIT
-->

# GB sequential market model on ASSUME: design spec (2026-11-02, revised 2026-11-03)

## Context
The goal is an ASSUME scenario for a GB-style sequential market. It is used to study how the markets interact:
day-ahead → intraday → hourly balancing energy → imbalance settlement.

- **Units:** solar, wind, gas, storage and demand, each with its own operator.
- **Strategies:** rule-based now. The interface leaves room to swap in learning strategies later.
- **Forecasts:** each market uses its own forecast vintage (DA, then the ID update, then the actual).
- **Imbalance:** imbalance cost (physical output minus sold position, at the balancing price) must feed into every unit's net result.
- **Run:** 1 week, hourly.

ASSUME today has none of these pieces:
- no forecast vintages;
- no imbalance settlement;
- no separation of financial position from physical output (`PowerPlant.execute_current_dispatch` clips `outputs["energy"]` in place);
- no balancing clearing that moves positions.

It also has gaps that affect this design:
- `profit` is overwritten per market and never exported.
- `energy_cashflow` sums DA and ID, so the two revenues can't be told apart.

**Revision 2026-11-03.** This revision makes four changes:
- The capacity (reserve) market is removed entirely.
- DA and ID move to new times.
- Every DA and ID product delivers on D+1.
- The battery uses a DA price forecast only. There is no perfect-foresight option.

## Agreed design

### 1. Markets (UTC; auctions on day D; delivery day D+1 00:00–24:00; hourly index)
| id | product_type | mechanism | open → gate | products | first delivery |
|---|---|---|---|---|---|
| DA | energy | `pay_as_clear` (existing) | 08:30 → 09:00 D | 24×1h | D+1 00:00 |
| ID | energy | `pay_as_clear` (existing) | 21:30 → 22:00 D | 24×1h | D+1 00:00 |
| BAL | balancing_energy | `balancing_pay_as_clear` (new) | H−70min → H−60min, hourly | 1×1h | H (60 min after gate) |

- **Why the timings are staggered:** bids are computed when a market opens (`assume/common/units_operator.py:208-223`), so each market opens after the previous one clears. BAL for D+1 00:00 opens at 22:50 D, after the ID gate.
- **First delivery:**
  - ASSUME's `first_delivery` is an offset from market opening (`assume/common/utils.py:99`, parsed with `pd.Timedelta` in `loader_csv.py:356`).
  - The config therefore uses DA `15h30m`, ID `2h30m` and BAL `1h10m`. These land exactly on D+1 00:00, D+1 00:00 and H.
  - A test asserts the product start times.
  - No anchored "next day" option is added, because `base_market.py:58-62` takes `max()` over offsets.
- **TSO:** there is no TSO agent. BAL builds its TSO order from the NIV (section 4).
- **Positions:**
  - DA and ID add into `outputs["energy"]`.
  - BAL's signed accepted volumes go into `outputs["balancing_energy"]`.
  - Position = energy + balancing_energy.
- **Run window:** the world starts at 00:00 the day before the first delivery day and runs 7 delivery days. The end date must fit `base_market.py:58-62` and `world.py:678-687`.

### Position, FPN, physical, imbalance (per unit, per hour)
Each unit carries four quantities per hour:
- **Position:** what it has sold net of buy-backs. DA + ID + accepted BAL.
- **FPN:** the physical output the unit notifies at the BAL gate, from its actual availability or actual demand.
- **Physical:** FPN + accepted BAL, i.e. what is metered.
- **Imbalance:** physical − position = FPN − (DA + ID). An accepted BAL action moves physical and position by the same amount, so it never creates or removes imbalance.

**Balancing leftovers.** A unit's leftover volume (FPN − DA − ID) is physical, so it is never offered. It counts automatically in the NIV and is settled at the BAL price.

**What units offer in BAL.** Only controllable flexibility: gas headroom and turn-down, battery charge and discharge headroom, and wind and solar curtailment.

**Example (wind, 100 MW, one hour):**
- DA forecast 80, so it sells 80.
- ID forecast 70, so it buys back 10. Position is now 70.
- Actual is 75, so FPN = 75.
- It is not called in BAL, so its imbalance is +5, paid at the BAL price.

### 2. Units, forecasts, data
- **Units:**
  - `powerplant_units.csv`: solar and wind (technology `solar`/`wind`; `wind_onshore` breaks the `is_renewable` regex), plus a gas CCGT;
  - `storage_units.csv`: battery;
  - `demand_units.csv`: demand with a fixed `price`.
  - Each unit has its own `unit_operator`.
- **Vintages:**
  - `availability_df.csv` and `demand_df.csv` hold the **actual** series.
  - New optional files hold the earlier vintages: `availability_da_df.csv`, `availability_id_df.csv`, `demand_da_df.csv`, `demand_id_df.csv`.
  - They are loaded in `assume/scenario/loader_csv.py` (≈651-740). If absent, they fall back to actual.
  - `PowerplantForecaster` and `DemandForecaster` (`assume/common/forecaster.py:390-458`) get a vintage store, with `get_availability(vintage)` and `get_demand(vintage)` for `da|id|actual`. `forecaster.availability`/`.demand` stay the actual series.
  - Each market picks its vintage via `param_dict.forecast_vintage`: DA=`da`, ID=`id`, BAL=`actual`.
- **Price forecast:**
  - The generator writes `forecasts_df.csv` with one column, `price_DA`, from a merit order of the example units against the **DA-vintage** demand and renewables. The existing lookup at `forecaster.py:71` picks it up.
  - Only the battery's DA bid uses it. ID and BAL bids use the realised DA price.
  - ASSUME's built-in naive forecast is not used, because it is computed from the actual series and would leak foresight.
- **Generator:** `examples/gb/generate_example_gb_01.py`.
  - Takes one week of 2019 shapes from `examples/inputs/example_01c/` (resampled to hourly, scaled).
  - DA forecast = actual·(1+ε_DA), with ε_DA an AR(1) process (`sigma_da`, `rho`).
  - ID forecast = actual·(1+λ·ε_DA+ε_ID), with λ<1 and a small `sigma_id`.
  - Clips availability to [0,1], uses a fixed seed, and writes the CSVs plus `.license` files.

### 3. Rule-based strategies (`assume/strategies/sequential_strategies.py`)
- **Base class:** `SequentialMarketStrategy(BaseStrategy)` serves DA, ID and BAL, with one instance per unit and the same strategy name in every market.
  - `calculate_bids` routes on product_type to `bid_energy` (DA and ID) or `bid_balancing` (BAL).
  - `bid_balancing` calls `decide_dispatch(unit, H)`, which returns the FPN, and attaches `fpn` and `position` to its orders.
  - Every method loops over **each** product tuple.
- **Energy bids are inc/dec around the current position P.** Sell up to `target − P`; offer to buy back `P − floor`.
- **Realised DA price:**
  - Every DA order comes back with `accepted_price` = the clearing price, even when it is rejected (`base_market.py:658-681`).
  - Strategies record it per hour from their own feedback in the existing `calculate_reward(unit, marketconfig, orderbook)` hook (`base.py:174-178`), which runs after every clearing. No new operator hook is needed.
  - BAL notification orders have zero volume, so strategies must not pass them through `remove_empty_bids`.
- **Price-taker buy price:** `market_config.maximum_bid_price`.

| unit (strategy name) | DA | ID | BAL | FPN (`decide_dispatch`) |
|---|---|---|---|---|
| gas `powerplant_sequential_heuristic` | Offer `max − P` at mc | Sell `max − P` at mc; buy back `P − min` at mc | Inc `max − P` at mc + `bal_markup`; dec `P − min` at mc − `bal_markup` | Position clipped to [min, max] |
| wind/solar `renewable_sequential_heuristic` | Sell `avail_da·max − P` at mc (≈0) | Trade to `avail_id·max`: sell surplus at mc; buy shortfall at the price cap | Dec (curtail) up to FPN at mc − `bal_markup` | `avail_actual·max` |
| battery `storage_sequential_heuristic` | Charge at ≤ v·η, discharge at ≥ v/η, volumes within SOC limits, incremental to P | Same rule incremental to P, with v from realised DA prices | Inc remaining discharge headroom at v/η + `bal_markup`; dec remaining charge headroom at v·η − `bal_markup` | Position clipped to the SOC limits |
| demand `demand_sequential_fixed` | Buy `demand_da − bought` at fixed `price` | Buy shortfall vs `demand_id` at `price`; sell surplus at `sellback_price` | Notification only (zero-volume order carrying FPN) | Actual demand |

- **P in the table** is the unit's position for that hour when it bids: `energy + balancing_energy`.
  - In DA, P = 0.
  - In ID, P = the DA accepted volume.
  - In BAL, P = DA + ID.
  - For storage, P is net discharge (positive) or charge (negative).
- **Rationale for each rule:**
  - **Gas at mc:** bidding its true cost is the default for a price-taker under pay-as-clear.
  - **Gas buy-back:** make-or-buy. If ID clears below mc, buying is cheaper than generating.
  - **Gas dec in BAL:** turning down means buying energy back from the TSO. That is worth it whenever the BAL price is below the fuel saved.
  - **Renewables:** always trade back to the latest forecast, because a unit can't predict the imbalance price better than ID.
  - **Battery:** the value of stored energy, v, is its marginal cost. It is the mean price over `storage_foresight`, taken from `price_DA` in DA and from realised DA prices (falling back to `price_DA`) in ID and BAL.
  - **Demand sell-back at 0:** it won't pay to dispose of energy. A surplus left open earns the BAL price anyway.
- **Gas simplification:** the example gas plant has min_power 0 and no ramp limits. A partially accepted hourly product could otherwise leave it below its minimum stable output.
- **Where volumes come from:**
  - Gas and storage reuse `calculate_min_max_power` (`assume/units/powerplant.py:288-352`) and `calculate_min_max_charge`/`_discharge` (`assume/units/storage.py:411-497`).
  - VRE and demand compute volumes from the vintage series directly, to avoid the actual series leaking into earlier markets.
- **Parameters** (`bidding_strategy_params` in the study case):

  | parameter | default |
  |---|---|
  | `bal_markup` | 0 |
  | `sellback_price` | 0 |
  | `storage_foresight` | "12h" |

  The demand `price` comes from `demand_units.csv`.
- **Registration:** in `assume/strategies/__init__.py`.

### 4. Balancing clearing, units, settlement
- **`balancing_pay_as_clear`** (`assume/markets/clearing_algorithms/balancing.py`, registered in `clearing_mechanisms`):
  - **Inputs:** `additional_fields: [fpn, position]`. Each unit sends ≥1 order per H; a demand unit sends a zero-volume notification order.
  - **NIV** = Σ_units(fpn − position).
    - NIV<0 (short): the TSO buys |NIV| against inc offers, ascending.
    - NIV>0 (long): the TSO sells NIV against dec bids, descending.
  - **Counterparty:** the TSO only. Crossed inc/dec pairs are never matched, so accepted volume = |NIV| in one direction.
  - **Price:** where NIV meets that side's stack (the marginal accepted price).
  - **Edge cases:**
    - NIV≈0: midpoint of the best inc and best dec.
    - Not enough offers: max/min bid price, with the unmet volume logged.
  - **Meta:** adds `niv`, `unmet_volume`, `up_volume` and `down_volume`, all as non-negative magnitudes except the signed `niv`.
- **Opt-in unit subclasses** (`assume/units/sequential_units.py`): `SequentialPowerPlant`, `SequentialStorage`, `SequentialDemand`.
  - Registered in `assume/units/__init__.py`.
  - Selected by a new config key `unit_types: {power_plant: sequential_power_plant, …}`, read at `loader_csv.py:1053-1077`. The default is unchanged.
  - `energy`/`balancing_energy` are financial and are never clipped. `fpn`/`physical` hold metered output. `execute_current_dispatch` returns `physical`, storage SOC moves on `physical`, and generation costs are computed from `physical`.
  - They extend `calculate_cashflow` to also write `cashflow_<market_id>` (using `order["market_id"]`), so DA and ID revenue can be told apart.
- **Settlement** in `UnitsOperator.handle_market_feedback` (`units_operator.py:225-248`), when `param_dict.imbalance_settlement: true`. For each unit and hour H:
  1. balancing_energy += accepted (signed);
  2. physical = fpn + accepted;
  3. imbalance = physical − (energy+balancing_energy) = fpn − energy;
  4. imbalance_cashflow = imbalance·P_bal (long is paid, short pays);
  5. profit = Σ cashflows (DA, ID, BAL, imbalance) − generation_costs, assigned with `=`;
  6. call the strategy hook `on_settlement(unit, H)`. It is a no-op for rule-based strategies; learning strategies compute their reward there.
- **World validation:** `world.py:757-767` uses `type(unit) in`, which rejects subclasses. It switches to `isinstance`.
- **Outputs:**
  - The `unit_dispatch` whitelist (`units_operator.py:386-397`) gains `profit`, `imbalance`, `physical`, `fpn` and `position`.
  - In sequential mode, unit dispatch is written only on BAL feedback, which avoids duplicate rows per product_type.

### 5. Deliverables & scope
- **Modules:** sequential strategies, sequential units and the balancing clearing.
- **Hooks:** small opt-in hooks in loader, units operator, world validation and registries.
- **Example:** `examples/inputs/example_gb_01/` plus its generator, and an `examples/examples.py` entry.
- **Docs:** a release-notes entry (`docs/source/release_notes.rst`) and a short docs page.
- **Out of scope:**
  - capacity/reserve markets;
  - perfect-foresight strategies;
  - learning strategies (only the hooks);
  - continuous ID;
  - networks;
  - several units per technology;
  - real GB data.

## Verification
- **New tests** in `tests/test_sequential_*.py` (core marker):
  - BAL clearing: short, long, ≈0, not enough offers, crossed stack ignored, notification-only.
  - Settlement signs; providers not penalised for accepted balancing.
  - DA/ID/BAL product start times land on D+1 00:00 and H.
  - `cashflow_DA` and `cashflow_ID` are separate and sum to `energy_cashflow`.
  - Storage SOC follows `physical`.
  - Vintage loading and fallback; the `unit_types` mapping.
  - Strategies:
    - loops over every product;
    - incremental volumes;
    - VRE and demand use the vintage series;
    - battery v uses realised DA prices in ID.
- **Integration:** `assume -s example_gb_01 -c base -db "sqlite:///./examples/local_db/assume_db.db"`, then check these per hour:
  - Σ physical = 0;
  - accepted BAL = |NIV|;
  - Σ imbalance = NIV;
  - a σ=0 run gives NIV=0 and no imbalance.
- **Regression:** `pytest -m "not require_learning and not require_network"` still passes, and `pre-commit run --all-files` is clean.

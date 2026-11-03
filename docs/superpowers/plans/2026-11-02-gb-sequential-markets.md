# GB Sequential Markets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a GB-style sequential market scenario to ASSUME: DA → ID → hourly balancing energy → per-unit imbalance settlement. It has five rule-based units, each with its own operator, and per-market forecast vintages.

**Architecture:**
- **Opt-in pieces.** All new behaviour is opt-in and leaves existing scenarios unchanged: new unit subclasses (selected by a config `unit_types` map), one new clearing mechanism, one new strategy module, and a settlement hook in `UnitsOperator` triggered by `param_dict.imbalance_settlement`.
- **Position vs physical.** Units keep the financial position (`energy`, `balancing_energy`) separate from physical output (`fpn`, `physical`).
- **Balancing.** Units submit their physical notification (FPN) with their BAL bids. The BAL clearing computes NIV from those notifications and clears against the TSO only.

**Tech Stack:** Python 3.11+, ASSUME (mango agents), pandas 3.0, NumPy, pytest (asyncio_mode=auto), SQLite for the integration run.

**Spec:** `docs/superpowers/specs/2026-11-02-gb-sequential-markets-design.md` (revised 2026-11-03)

## Global Constraints

- **Versions:** Python 3.11–3.14; pandas `>=3.0,<3.1`. Add no new dependencies.
- **File headers:** every new `.py` file starts with the repo's SPDX header (`# SPDX-FileCopyrightText: ASSUME Developers` / `#` / `# SPDX-License-Identifier: MIT`).
- **Licences:** every new CSV in `examples/inputs/` gets a sibling `.license` file. Copy `examples/inputs/example_01c/demand_df.csv.license`.
- **Test markers:** new tests need neither torch nor pypsa, so they must pass under `pytest -m "not require_learning and not require_network"`.
- **Backwards compatibility:** existing behaviour is unchanged unless a scenario opts in through `unit_types`, `imbalance_settlement`, the new mechanism or the new strategies. Run `pytest -m "not require_learning and not require_network"` after every task.
- **No capacity/reserve market and no perfect-foresight option.** Don't add `capacity_pos`/`capacity_neg` logic to the new code.
- **Sign convention:**
  - `volume > 0` = supply/sell/inc; `volume < 0` = demand/buy/dec.
  - Imbalance > 0 = long (paid P_bal); < 0 = short (pays P_bal).
- **Units:** MW for power, £/MWh for prices. All products are 1h on a 1h index, so cashflow per hour = price × MW.
- **Market timings (UTC).** Auctions run on day D; delivery is D+1 00:00–24:00. `first_delivery` is an offset from opening:

  | Market | product_type | mechanism | opens → gate | products | `first_delivery` | `forecast_vintage` |
  |---|---|---|---|---|---|---|
  | DA | energy | `pay_as_clear` | 08:30 → 09:00 D | 24×1h | 15h30m | `da` |
  | ID | energy | `pay_as_clear` | 21:30 → 22:00 D | 24×1h | 2h30m | `id` |
  | BAL | balancing_energy | `balancing_pay_as_clear` | H−70min → H−60min, hourly | 1×1h | 1h10m | `actual` |

- **Strategy parameters** (`bidding_strategy_params` in the study case; one shared dict):

  | parameter | default |
  |---|---|
  | `bal_markup` | 0.0 |
  | `sellback_price` | 0.0 |
  | `storage_foresight` | "12h" |

## Review Focus

1. **Run-window edges.** Every one of the 168 delivery hours gets exactly one BAL clearing. The first BAL product is the first delivery hour (2019-01-08 00:00), and no DA or ID market opens for deliveries past `end_date`. Pinned in Task 9 (`test_gb_example_run_invariants`, `test_gb_market_products_start_d_plus_1`).
2. **Storage at SOC bounds.** At `min_soc`, the battery offers no inc and its FPN cannot discharge. At `max_soc`, there is no dec and no charging. Otherwise `physical` drives SOC out of [min_soc, max_soc]. Pinned in Task 8 (`test_storage_no_inc_at_min_soc`, `test_storage_no_dec_at_max_soc`).
3. **Battery without a price reference.** If `forecaster.price` has no `"DA"` entry, or an hour has no realised DA price, the battery falls back as follows: realised DA price → `price_DA` forecast → no energy bid for that hour. It must never raise `KeyError`. Pinned in Task 8 (`test_storage_missing_prices_no_keyerror`).
4. **Zero-volume BAL notifications reach the clearing.** Demand's only BAL order has volume 0. If that order is stripped (by `remove_empty_bids` or `validate_orderbook`), demand's FPN drops out of NIV and Σ imbalance ≠ NIV. Pinned in Task 4 (`test_balancing_validate_keeps_notification`) and Task 6 (`test_demand_balancing_notification_only`).
5. **BAL with no offers on the needed side.** NIV ≠ 0 but nobody offers in that direction. The price must go to `maximum_bid_price` (short) or `minimum_bid_price` (long), with `unmet_volume` in meta. It must not crash or silently return price 0. Pinned in Task 4 (`test_balancing_unmet_short_uses_cap`).

---

### Task 1: Forecast vintages on forecasters

**Files:**
- Modify: `assume/common/forecaster.py` (`UnitForecaster.__init__` ~174-202, `DemandForecaster` ~390-420)
- Test: `tests/test_sequential_forecaster.py`

**Interfaces:**
- Produces:
  - `UnitForecaster.__init__(..., availability_vintages: dict[str, ForecastSeries] | None = None)`
  - `UnitForecaster.get_availability(vintage: str = "actual") -> FastSeries`
  - `DemandForecaster.__init__(..., demand_vintages: dict[str, ForecastSeries] | None = None)`
  - `DemandForecaster.get_demand(vintage: str = "actual") -> FastSeries`
  - `PowerplantForecaster.__init__` forwards `availability_vintages` to the base class.
- Semantics: `"actual"`, or any vintage that wasn't supplied, returns `self.availability` / `self.demand`. Each vintage series gets the same validation as the actual series (availability in [0,1]; demand ≤ 0).

- [ ] **Step 1: Write failing tests**

```python
def test_get_availability_vintages():
    idx = pd.date_range("2019-01-01", periods=3, freq="h")
    f = PowerplantForecaster(idx, availability=[1.0, 0.5, 0.2],
                             availability_vintages={"da": [0.9, 0.6, 0.1]})
    assert list(f.get_availability("da")) == [0.9, 0.6, 0.1]
    assert list(f.get_availability("actual")) == [1.0, 0.5, 0.2]
    assert list(f.get_availability("id")) == [1.0, 0.5, 0.2]  # fallback

def test_get_demand_vintages_and_validation():
    idx = pd.date_range("2019-01-01", periods=2, freq="h")
    f = DemandForecaster(idx, demand=[-10, -20], demand_vintages={"id": [-11, -19]})
    assert list(f.get_demand("id")) == [-11, -19]
    assert list(f.get_demand("da")) == [-10, -20]
    with pytest.raises(ValidationError):
        DemandForecaster(idx, demand=[-1, -1], demand_vintages={"da": [5, -1]})
```

- [ ] **Step 2:** Run `pytest tests/test_sequential_forecaster.py -v`. Expected: FAIL (unexpected keyword argument).
- [ ] **Step 3:** Implement the interfaces above. Convert each vintage with `self._to_series`.
- [ ] **Step 4:** Run `pytest tests/test_sequential_forecaster.py tests/test_forecast_interface.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(forecaster): add availability/demand forecast vintages`

---

### Task 2: Loader support for vintage files and `unit_types` mapping

**Files:**
- Modify: `assume/scenario/loader_csv.py` (forecaster creation ~651-740; `setup_world` `read_units` calls ~1050-1077)
- Modify: `assume/world.py:757-767`. Change `type(unit) in demand_types` / `generation_types` to `isinstance` checks, so subclasses count.
- Test: `tests/test_sequential_loader.py`

**Interfaces:**
- Consumes: Task 1 `availability_vintages`, `demand_vintages`.
- Produces:
  - **Optional input files:** `availability_da_df.csv`, `availability_id_df.csv`, `demand_da_df.csv`, `demand_id_df.csv`. They use the same format as `availability_df.csv` / `demand_df.csv` and are loaded with `load_file`.
    - Each column becomes the vintage of the unit it names.
    - Demand vintages are passed as `-abs(...)`.
    - Availability vintages > 1 are normalised with `normalize_availability`, like the actual series.
  - **Config key:** `unit_types: {power_plant: <registered name>, storage: ..., demand: ...}` (optional) in the study case. `setup_world` passes `unit_types.get("power_plant", "power_plant")` etc. as `unit_type` to `read_units`.

- [ ] **Step 1: Write failing tests**
  - `test_vintage_files_loaded`: copy `examples/inputs/example_01c` to `tmp_path` and write `availability_da_df.csv` with column `Wind Onshore` = 0.123. Call `load_config_and_create_forecaster(tmp_path, "example_01c", "eom_only")`. Assert `scenario_data["unit_forecasts"]["Wind Onshore"].get_availability("da").at[first_ts] == 0.123` and that `get_availability("id")` equals the actual availability.
  - `test_unit_types_mapping`: register a dummy `class MyPP(PowerPlant)` as `world.unit_types["my_pp"]`. Set `config["unit_types"] = {"power_plant": "my_pp"}` in the loaded `scenario_data`, run `setup_world`, and assert every power plant unit `isinstance(u, MyPP)`. World validation must not reject them.
- [ ] **Step 2:** Run `pytest tests/test_sequential_loader.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the loader changes and the `world.py` isinstance fix.
- [ ] **Step 4:** Run `pytest tests/test_sequential_loader.py tests/test_loader_csv.py tests/test_world_validation.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(loader): load forecast vintage files and support unit_types mapping`

---

### Task 3: Sequential unit subclasses (position vs physical, cashflow, settlement)

**Files:**
- Create: `assume/units/sequential_units.py`
- Modify: `assume/units/__init__.py`. Register `"sequential_power_plant"`, `"sequential_storage"`, `"sequential_demand"`.
- Test: `tests/test_sequential_units.py`

**Interfaces:**
- Produces `class SequentialUnitMixin`, used as `class SequentialPowerPlant(SequentialUnitMixin, PowerPlant)`, `SequentialStorage(SequentialUnitMixin, Storage)` and `SequentialDemand(SequentialUnitMixin, Demand)`. It provides:
  - `set_dispatch_plan(marketconfig, orderbook) -> None`: only `BaseUnit.set_dispatch_plan`. No SOC recompute and no clipping of the position.
  - `calculate_cashflow(product_type: str, orderbook: Orderbook) -> None`: calls `BaseUnit.calculate_cashflow`, then also adds `accepted_price * accepted_volume` to `outputs[f"cashflow_{order['market_id']}"]` for every hour of the order (when `market_id` is present).
  - `net_position(t) -> float` = `outputs["energy"].at[t] + outputs["balancing_energy"].at[t]`
  - `execute_current_dispatch(start, end) -> np.ndarray`: returns `outputs["physical"].loc[start:end]` and never modifies `energy`.
  - `calculate_generation_cost(start, end, product_type) -> None`: does nothing. Costs are written in `settle`.
  - `settle(t: datetime, fpn: float, accepted_balancing: float, price: float) -> None`. It sets, at time `t`:
    - `fpn` = fpn
    - `physical` = fpn + accepted_balancing
    - `position` = `net_position(t)` (after BAL was booked)
    - `imbalance` = physical − position
    - `imbalance_cashflow` = imbalance × price
    - `generation_costs` = `settlement_cost(t, physical)`
    - `profit` = Σ of every `outputs` key ending in `"_cashflow"` at `t` − `generation_costs`, assigned with `=`
  - `settlement_cost(t, physical) -> float`:
    - default `abs(calculate_marginal_cost(t, physical) * physical)`;
    - `SequentialDemand` returns 0.0.
- `SequentialStorage.settle` also sets `soc[t+freq]` from `physical`, using the same discharge/charge efficiency formula as `Storage.execute_current_dispatch`. It clips to [min_soc, max_soc] and logs a warning if clipping was needed.
- The `_cashflow` keys are `energy_cashflow`, `balancing_energy_cashflow` and `imbalance_cashflow`. `cashflow_<market_id>` keys don't end in `_cashflow`, so nothing is counted twice.

- [ ] **Step 1: Write failing tests** (fixtures as in `tests/test_powerplant.py`, 24h hourly index)
  - `test_per_market_cashflow_split`: DA order (market_id "DA", 1h, price 40, vol 100) and ID order (market_id "ID", same hour, price 60, vol −20). Assert `energy_cashflow` = 2800, `cashflow_DA` = 4000, `cashflow_ID` = −1200.
  - `test_execute_does_not_clip_position`: set `outputs["energy"]` to 900 with availability 0.5 (max 1000), and `physical` to 500. Assert `execute_current_dispatch` returns 500 and `outputs["energy"]` is still 900.
  - `test_settle_short_pays`: position energy=100, BAL accepted 0, fpn 80, price 200. Assert imbalance −20, imbalance_cashflow −4000, physical 80.
  - `test_settle_provider_not_penalised`: energy 100. BAL `set_dispatch_plan` accepted +30 (balancing_energy=30); fpn 100; price 150. Assert physical 130, imbalance 0, imbalance_cashflow 0.
  - `test_settle_profit_sums_components`: unit with energy_cashflow 4000, a BAL acceptance and an imbalance. Assert profit == 4000 + balancing_energy_cashflow + imbalance_cashflow − generation_costs.
  - `test_storage_soc_follows_physical`: SequentialStorage. `set_dispatch_plan` with energy +50 must leave `soc` unchanged. Then `settle(t, fpn=50, accepted_balancing=0, price=0)` reduces `soc[t+1]` by `50/eff_dis/capacity`.
- [ ] **Step 2:** Run `pytest tests/test_sequential_units.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement the module and the registry entries.
- [ ] **Step 4:** Run `pytest tests/test_sequential_units.py tests/test_baseunit.py tests/test_storage.py tests/test_powerplant.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(units): add sequential unit subclasses separating position from physical output`

---

### Task 4: `balancing_pay_as_clear` clearing

**Files:**
- Create: `assume/markets/clearing_algorithms/balancing.py`
- Modify: `assume/markets/clearing_algorithms/__init__.py` (register `"balancing_pay_as_clear": BalancingPayAsClearRole`)
- Modify: `tests/test_simple_market_mechanisms.py:73`. Add `"balancing"` to the skip list, because the generic orderbook has no `fpn`/`position`.
- Test: `tests/test_sequential_clearing.py`

**Interfaces:**
- Produces `class BalancingPayAsClearRole(MarketRole)` with `clear(orderbook, market_products) -> (accepted, rejected, meta, flows)`. It requires `additional_fields: ["fpn", "position"]`.
- Algorithm per product:
  1. Group orders by `unit_id`. Each unit's `fpn`/`position` come from its first order. NIV = Σ(fpn − position). Use tolerance `1e-6`.
  2. **NIV < 0 (short):** need = −NIV. Take incs (volume > 0) in ascending price order (random tie-break, as in `PayAsClearRole`). Accept until the need is met; the last one may be partial. P = price of the last accepted order. If need remains: P = `maximum_bid_price` and `unmet_volume` = remainder.
  3. **NIV > 0 (long):** need = NIV. Take decs (volume < 0) in descending price order. Accept |volume| until the need is met. P = price of the last accepted order. If need remains: P = `minimum_bid_price`.
  4. **|NIV| ≤ tol:** accept nothing. P = midpoint of the cheapest inc and the most expensive dec. If only one side exists, use its best price; if neither, 0.
  5. Incs and decs are never matched against each other. Every order (accepted or rejected) gets `accepted_price = P`; rejected orders get `accepted_volume = 0`.
  6. Meta follows the keys of `simple.calculate_meta`, plus `niv` (signed), `unmet_volume`, `up_volume` and `down_volume`. The last three are non-negative magnitudes. `price` = P.

- [ ] **Step 1: Write failing tests** (helper `bal_order(unit, volume, price, fpn, position)`, one 1h product, `maximum_bid_price=3000`, `minimum_bid_price=-500`)
  - `test_balancing_short`: vre fpn 80 / pos 100 (notification volume 0); gas inc 50 @ 90 and inc 30 @ 120. NIV −20. Assert gas inc @ 90 accepted 20, P 90, `meta.niv == -20`.
  - `test_balancing_long`: vre fpn 130 / pos 100; gas dec −40 @ 30, storage dec −50 @ 10. NIV +30. Assert gas dec accepted −30, storage rejected, P 30.
  - `test_balancing_zero_niv_midpoint`: NIV 0, inc @ 90 and dec @ 30. Assert nothing accepted, P 60.
  - `test_balancing_crossed_stack_not_netted`: NIV −10; storage inc 50 @ 5, gas dec −50 @ 55. Assert storage accepted 10, gas dec rejected, P 5.
  - `test_balancing_unmet_short_uses_cap` *(Review Focus 5)*: NIV −20 and only one inc of 5 @ 90. Assert accepted 5, P 3000, `unmet_volume == 15`.
  - `test_balancing_notification_only`: all orders have volume 0 and NIV 0. Assert no accepted orders and P 0.
  - `test_balancing_validate_keeps_notification` *(Review Focus 4)*: create the role with a BAL MarketConfig, add the product to `role.open_auctions`, and call `validate_orderbook` on one zero-volume order carrying `fpn`/`position`. Assert no exception and that the order is still there.
- [ ] **Step 2:** Run `pytest tests/test_sequential_clearing.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `pytest tests/test_sequential_clearing.py tests/test_simple_market_mechanisms.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(markets): add TSO-only single-price balancing clearing from physical notifications`

---

### Task 5: Settlement hook in UnitsOperator

**Files:**
- Modify: `assume/common/units_operator.py` (`handle_market_feedback` 225-248, `write_actual_dispatch` 404-418, whitelist 386-392)
- Modify: `assume/common/base.py`. Add a no-op `BaseStrategy.on_settlement(self, unit, start: datetime) -> None`.
- Test: `tests/test_sequential_units_operator.py`

**Interfaces:**
- Consumes: Task 3 `SequentialUnitMixin.settle(t, fpn, accepted_balancing, price)`.
- Produces:
  - `UnitsOperator.settle_imbalance(orderbook: Orderbook, marketconfig: MarketConfig) -> None`. It groups orders by `unit_id`, and for each unit and each product start `t` it:
    - sets `fpn` = order `fpn`;
    - sets `accepted_balancing` = Σ `accepted_volume` over the unit's orders for `t`;
    - sets `price` = `accepted_price` of any order;
    - calls `unit.settle(...)`, then `unit.bidding_strategies[marketconfig.market_id].on_settlement(unit, t)`.
  - `handle_market_feedback` order: `set_unit_dispatch` → `calculate_unit_cashflow_and_reward` → (if `marketconfig.param_dict.get("imbalance_settlement")`) `settle_imbalance` → `write_actual_dispatch`. Moving the write after settlement is safe: `write_actual_dispatch` only reads outputs. `calculate_unit_cashflow_and_reward` keeps calling `strategy.calculate_reward` for every market; Task 6 relies on this.
  - **Sequential mode:** if any registered market has `param_dict.imbalance_settlement`, then `write_actual_dispatch` is skipped for feedback from every other market.
  - **Whitelist:** add `"profit"`, `"imbalance"`, `"physical"`, `"fpn"` and `"position"`. Substring matching already covers `imbalance_cashflow`.
  - **Compatibility:** non-sequential units don't receive `settle` calls, because no existing scenario sets the flag.

- [ ] **Step 1: Write failing tests** (adapt the `units_operator` fixture from `tests/test_units_operator.py`, using a SequentialDemand plus a BAL MarketConfig with `param_dict={"imbalance_settlement": True}`, `product_type="balancing_energy"`)
  - `test_handle_feedback_settles`: demand position energy −100 (bought). A BAL feedback orderbook has one notification order for the demand with fpn −110, position −100, accepted_volume 0, accepted_price 200. Call `handle_market_feedback`. Assert `outputs["imbalance"].at[t] == -10` and `outputs["imbalance_cashflow"].at[t] == -2000`.
  - `test_non_settlement_market_does_not_settle`: the same orderbook through a market without the flag leaves `outputs["imbalance"]` at 0.
- [ ] **Step 2:** Run `pytest tests/test_sequential_units_operator.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run `pytest tests/test_sequential_units_operator.py tests/test_units_operator.py tests/test_outputs.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(units_operator): settle imbalance on balancing feedback and export profit`

---

### Task 6: Strategy base, demand and renewable strategies

**Files:**
- Create: `assume/strategies/sequential_strategies.py`
- Modify: `assume/strategies/__init__.py`. Register `"demand_sequential_fixed"` and `"renewable_sequential_heuristic"` in `bidding_strategies`.
- Test: `tests/test_sequential_strategies.py`

**Interfaces:**
- Consumes: Task 1 `get_availability(vintage)` / `get_demand(vintage)`; Task 3 `net_position(t)`.
- Produces `class SequentialMarketStrategy(BaseStrategy)`:
  - `__init__(self, *args, **kwargs)` reads `bal_markup`, `sellback_price` and `storage_foresight` from kwargs, with the Global Constraints defaults. It ignores unknown kwargs and initialises `self.realised_prices: dict[str, dict[datetime, float]] = {}`.
  - `calculate_bids(unit, market_config, product_tuples, **kwargs) -> Orderbook` routes on `market_config.product_type`:
    - `energy` → `bid_energy`;
    - `balancing_energy` → `bid_balancing`;
    - anything else → `[]`.
  - `vintage(market_config) -> str` = `market_config.param_dict.get("forecast_vintage", "actual")`.
  - `calculate_reward(unit, marketconfig, orderbook) -> None` records `self.realised_prices[marketconfig.market_id][order["start_time"]] = order["accepted_price"]` for every order, accepted or rejected.
  - `realised_price(market_id: str, t: datetime) -> float | None` returns the recorded price, or `None`.
  - `bid_energy(unit, market_config, product_tuples) -> Orderbook` (abstract).
  - `bid_balancing(unit, market_config, product_tuples) -> Orderbook` (concrete):
    - for each product, it calls `decide_dispatch(unit, start)`, then `balancing_offers(unit, market_config, product, fpn)`;
    - if there are no offers, it emits one notification order (volume 0, price 0);
    - it sets `fpn` and `position = unit.net_position(start)` on every order;
    - it never calls `remove_empty_bids`.
  - `balancing_offers(unit, market_config, product, fpn) -> Orderbook` (default `[]`) and `decide_dispatch(unit, start) -> float` (abstract).
  - `on_settlement(unit, start) -> None` is a no-op.
  - Orders are dicts with `start_time`, `end_time`, `only_hours`, `price`, `volume` and `node` (`unit.node`). There is one order per product per direction; energy bids omit zero-volume directions.
- `class DemandSequentialFixedStrategy(SequentialMarketStrategy)`:
  - **energy:** per product, `target = forecaster.get_demand(vintage).at[t]` (≤ 0) and `delta = target − outputs["energy"].at[t]`. If `delta < 0`, buy `delta` at `unit.price` (the demand unit's fixed bid price). If `delta > 0`, sell back `delta` at `sellback_price`.
  - **decide_dispatch:** `forecaster.get_demand("actual").at[t]`.
- `class RenewableSequentialHeuristicStrategy(SequentialMarketStrategy)`:
  - **energy:** `target = get_availability(vintage).at[t] * unit.max_power`, `delta = target − energy`. If `delta > 0`, sell at `unit.calculate_marginal_cost(t, 0)`. If `delta < 0`, buy back at `market_config.maximum_bid_price` (price-taker).
  - **decide_dispatch:** `get_availability("actual").at[t] * max_power`.
  - **balancing_offers:** a dec of `−fpn` at `mc − bal_markup`, when fpn > 0.

- [ ] **Step 1: Write failing tests** (SequentialDemand / SequentialPowerPlant fixtures with vintages: actual 0.5, da 0.8, id 0.6, max_power 100; MarketConfigs for DA/ID/BAL with `param_dict.forecast_vintage`, `maximum_bid_price=3000`)
  - `test_renewable_da_uses_da_vintage`: the DA bid volume is 80 @ mc.
  - `test_renewable_id_buys_back_at_cap`: energy already 80. The ID bid is volume −20 @ 3000.
  - `test_renewable_balancing_fpn_and_dec`: with `bal_markup=10`, the BAL orders carry fpn 50 and position 80, and there is one dec of −50 @ mc − 10.
  - `test_demand_fixed_price_and_sellback`: demand da −100 and id −90, `price` 3000. The DA bid is −100 @ 3000. After energy = −100, the ID bid is +10 @ 0.
  - `test_demand_balancing_notification_only` *(Review Focus 4)*: exactly one order, volume 0, with fpn = actual demand and position = current energy.
  - `test_realised_price_recorded_from_rejected_order`: call `calculate_reward` with a DA orderbook holding one rejected order (accepted_volume 0, accepted_price 55). `realised_price("DA", t) == 55`.
  - `test_one_bid_per_product`: with 24 products, `len(bids) == 24` and each bid's `end_time` equals its own product's end.
- [ ] **Step 2:** Run `pytest tests/test_sequential_strategies.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement and register.
- [ ] **Step 4:** Run `pytest tests/test_sequential_strategies.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(strategies): add sequential strategy base with demand and renewable heuristics`

---

### Task 7: Gas (power plant) sequential strategy

**Files:**
- Modify: `assume/strategies/sequential_strategies.py`, `assume/strategies/__init__.py` (register `"powerplant_sequential_heuristic"`)
- Test: `tests/test_sequential_strategies.py`

**Interfaces:**
- Consumes: Task 6 base; `PowerPlant.calculate_min_max_power(start, end)` (`assume/units/powerplant.py:288`), which already subtracts `energy`.
- Produces `class PowerplantSequentialHeuristicStrategy(SequentialMarketStrategy)`:
  - **bid_energy:** per hour, take `(min_p, max_p)` from `calculate_min_max_power(t, t+freq)`. Sell `max_p` (= available max − P) at mc when `max_p > 0`. Buy back `min_p` (= min_power − P, negative when P > min_power) at mc when `min_p < 0`. In DA, P = 0, so only the sell order appears.
  - **decide_dispatch:** `energy.at[t]` (the DA+ID position), clipped to `[min_power, availability·max_power]`. The example sets gas `min_power` 0 and no ramp limits, as the spec's gas simplification describes.
  - **balancing_offers:**
    - inc: `availability·max_power − fpn` at `mc + bal_markup`, when > 0;
    - dec: `−(fpn − min_power)` at `mc − bal_markup`, when fpn > min_power.

- [ ] **Step 1: Write failing tests** (SequentialPowerPlant: max 1000, min 0, mc ≈ 74.5)
  - `test_gas_da_offers_full_capacity`: energy 0. The DA bids are exactly one order per hour, +1000 @ mc.
  - `test_gas_id_incremental`: energy 600. The ID bids are +400 @ mc and −600 @ mc.
  - `test_gas_balancing_inc_dec`: energy 700, `bal_markup=10`. BAL has inc +300 @ mc+10, dec −700 @ mc−10, and fpn 700.
  - `test_gas_fpn_follows_position`: energy 400 gives fpn 400.
- [ ] **Step 2:** Run `pytest tests/test_sequential_strategies.py -v -k gas`. Expected: FAIL.
- [ ] **Step 3:** Implement and register.
- [ ] **Step 4:** Run `pytest tests/test_sequential_strategies.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(strategies): add gas sequential heuristic`

---

### Task 8: Storage sequential strategy

**Files:**
- Modify: `assume/strategies/sequential_strategies.py`, `assume/strategies/__init__.py` (register `"storage_sequential_heuristic"`)
- Test: `tests/test_sequential_strategies.py`

**Interfaces:**
- Consumes: `Storage.calculate_min_max_charge(start, end, soc)` and `calculate_min_max_discharge(start, end, soc)` (`assume/units/storage.py:411,453`); Task 3 `net_position`, `outputs["soc"]`; Task 6 `realised_price`.
- Produces `class StorageSequentialHeuristicStrategy(SequentialMarketStrategy)`:
  - `planned_soc(unit, t) -> float`: takes the latest settled `soc` at or before `t` (the last index where `outputs["physical"]` was set, or `soc` at index[0]). It rolls that forward to `t` with `net_position` and the storage efficiencies.
  - `reference_price(unit, market_config, t) -> float | None`:
    - in DA (`vintage == "da"`), `unit.forecaster.price["DA"].at[t]`;
    - in ID and BAL, `realised_price("DA", t)`, falling back to the forecast;
    - `None` if neither exists. It never raises `KeyError`.
  - `energy_value(unit, market_config, t) -> float | None`: v = the mean of the non-None `reference_price` values over `[t − storage_foresight, t + storage_foresight]`, or `None` if there are none.
  - **bid_energy** (DA and ID, same rule): walk the products in order with a running theoretic SOC, starting from `planned_soc(first start)`. For each hour, with `p = reference_price` and `v = energy_value`:
    - skip the hour if either is `None`;
    - if `p ≥ v / eff_dis`: discharge bid of the remaining discharge headroom at `v / eff_dis`;
    - else if `p ≤ v · eff_ch`: charge bid of the remaining charge headroom at `v · eff_ch`.
    - Headroom comes from `calculate_min_max_discharge` / `calculate_min_max_charge(t, t+freq, soc=theoretic)`, so it is incremental to P. Advance the theoretic SOC as though the bid is accepted.
  - **decide_dispatch:** `net_position(t)` clipped to the SOC-feasible range at `planned_soc(t)`.
  - **balancing_offers:** with v from `energy_value`, and nothing offered if v is `None`:
    - inc: the remaining SOC-feasible discharge above fpn at `v / eff_dis + bal_markup`;
    - dec: the remaining SOC-feasible charge below fpn at `v · eff_ch − bal_markup`;
    - both capped by power limits.

- [ ] **Step 1: Write failing tests** (SequentialStorage 100 MW / 200 MWh, eff 0.9/0.9, min_soc 0, max_soc 1)
  - `test_storage_no_inc_at_min_soc` *(Review Focus 2)*: soc = min_soc. There are no BAL orders with volume > 0, and fpn ≤ 0.
  - `test_storage_no_dec_at_max_soc` *(Review Focus 2)*: soc = max_soc. There are no BAL orders with volume < 0.
  - `test_storage_arbitrage_da`: a `price_DA` forecast of 20 for hours 0-5 and 100 for hours 6-11 makes the hour 0-5 bids charges (volume < 0) and the hour 6-11 bids discharges (volume > 0), with SOC never planned outside [0,1].
  - `test_storage_id_uses_realised_da_price`: the forecast is flat at 50, but the realised DA prices recorded via `calculate_reward` are 20 for hours 0-5 and 100 for hours 6-11. The ID bids charge in hours 0-5 and discharge in hours 6-11.
  - `test_storage_missing_prices_no_keyerror` *(Review Focus 3)*: `forecaster.price` has no `"DA"` key and there are no realised prices. `calculate_bids` for DA, ID and BAL returns no energy bids and only a BAL notification order, and raises nothing.
- [ ] **Step 2:** Run `pytest tests/test_sequential_strategies.py -v -k storage`. Expected: FAIL.
- [ ] **Step 3:** Implement and register.
- [ ] **Step 4:** Run `pytest tests/test_sequential_strategies.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit: `feat(strategies): add storage sequential heuristic with SOC-aware balancing`

---

### Task 9: Synthetic data generator, example scenario, integration test

**Files:**
- Create: `examples/gb/generate_example_gb_01.py`
- Create (generated, committed): `examples/inputs/example_gb_01/{config.yaml, powerplant_units.csv, storage_units.csv, demand_units.csv, availability_df.csv, availability_da_df.csv, availability_id_df.csv, demand_df.csv, demand_da_df.csv, demand_id_df.csv, fuel_prices_df.csv, forecasts_df.csv}`, each CSV with a `.license` sibling
- Modify: `examples/examples.py`. Add `"gb_sequential": {"scenario": "example_gb_01", "study_case": "base"}`.
- Test: `tests/test_sequential_integration.py` (`@pytest.mark.slow` on the simulation runs only)

**Interfaces:**
- Consumes: everything above.
- **Generator CLI:** `python examples/gb/generate_example_gb_01.py [--seed 42] [--sigma-da 0.15] [--sigma-id 0.05] [--rho 0.8] [--lam 0.3] [--out examples/inputs/example_gb_01]`
  - **Source data:** `examples/inputs/example_01c/availability_df.csv` (columns `Wind Onshore`, `Solar`) and `demand_df.csv` (column `demand_EOM`), sliced to `2019-01-07 00:00`–`2019-01-15 00:00` and resampled to 1h mean.
  - **Scaling:** demand scaled to a 1000 MW peak. Units: wind 500 MW, solar 300 MW, gas 1000 MW (min_power 0, efficiency 0.55, fuel `natural gas`, emission_factor 0.2), battery 100 MW / 200 MWh (eff 0.9/0.9, initial_soc 0.5), demand `price` 3000.
  - **Fuel prices:** `natural gas` 25 and `co2` 80.
  - **Errors:** ε_DA[t] = ρ·ε_DA[t−1] + N(0, σ_DA·√(1−ρ²)). The ID error is λ·ε_DA + N(0, σ_ID). Availability is clipped to [0,1], and solar vintages are 0 wherever the actual is 0.
  - **Price forecast** (`forecasts_df.csv`, one column `price_DA`). It is a merit order on the DA vintage, with `rl = demand_da − wind_da − solar_da` (MW) and gas_mc = 25/0.55 + 80·0.2/0.55:
    - `price_DA = 0` if rl ≤ 0;
    - `gas_mc` if 0 < rl ≤ 1000;
    - `3000` otherwise.
  - **Unit CSVs:** each unit has its own `unit_operator` (`op_solar`, `op_wind`, `op_gas`, `op_battery`, `op_demand`). Technologies are `solar`, `wind`, `CCGT`, `battery`, `inflex_demand`. Columns `bidding_DA`, `bidding_ID` and `bidding_BAL` each hold the unit's single sequential strategy name.
- **`config.yaml` study case `base`:**
  - `start_date: 2019-01-07 00:00`, `end_date: 2019-01-15 00:00`, `time_step: 1h`.
  - `unit_types: {power_plant: sequential_power_plant, storage: sequential_storage, demand: sequential_demand}`.
  - The three markets use the Global Constraints timings, `first_delivery` values and `forecast_vintage`s, with market `start_date`s 2019-01-07 08:30 (DA, `opening_frequency: 24h`, `opening_duration: 30m`), 2019-01-07 21:30 (ID, `24h`, `30m`) and 2019-01-07 22:50 (BAL, `1h`, `10m`).
  - BAL `additional_fields: [fpn, position]` and `param_dict: {imbalance_settlement: true, forecast_vintage: actual}`.
  - All markets: `maximum_bid_price: 3000`, `minimum_bid_price: -500`, `maximum_bid_volume: 100000`.
  - `bidding_strategy_params: {bal_markup: 0, sellback_price: 0, storage_foresight: 12h}`.

- [ ] **Step 1:** Write the generator and run `python examples/gb/generate_example_gb_01.py`. Expected: the CSVs and their `.license` files exist, and `git status` shows the new folder.
- [ ] **Step 2: Write the failing tests**
  - `test_gb_market_products_start_d_plus_1` (not slow; Review Focus 1): read `examples/inputs/example_gb_01/config.yaml` and build each market with `make_market_config(id, params, world_start, world_end)` (`assume/scenario/loader_csv.py:324`). For each market, call `get_available_products(config.market_products, config.opening_hours[0])`. Assert:
    - DA's first product starts at 2019-01-08 00:00 and its last ends at 2019-01-09 00:00;
    - ID's first product starts at 2019-01-08 00:00;
    - BAL's single product, opening at 2019-01-07 22:50, starts at 2019-01-08 00:00 and ends at 01:00.
  - The two slow run tests:

```python
@pytest.mark.slow
def test_gb_example_run_invariants(tmp_path):
    db = f"sqlite:///{tmp_path}/gb.db"
    world = World(database_uri=db)
    load_scenario_folder(world, "examples/inputs", "example_gb_01", "base")
    world.run()
    meta = pd.read_sql("select * from market_meta where market_id='BAL'", db)
    disp = pd.read_sql("select * from unit_dispatch", db)
    assert len(meta) == 168                                   # Review Focus 1
    assert pd.Timestamp(meta.product_start.min()) == pd.Timestamp("2019-01-08 00:00")
    ok = meta.assign(product_start=pd.to_datetime(meta.product_start)).set_index("product_start")
    sums = disp.assign(time=pd.to_datetime(disp.time)).groupby("time")[["physical", "imbalance"]].sum()
    # ASSUME has no end-of-run dispatch flush, so the last 1-2 hours may be missing from unit_dispatch
    hours = ok.index.intersection(sums.index)
    assert len(hours) >= 165
    full = hours[ok.loc[hours, "unmet_volume"].abs() < 1e-6]
    assert sums.loc[full, "physical"].abs().max() < 1e-6                         # Σ physical = 0
    assert (sums.loc[hours, "imbalance"] - ok.loc[hours, "niv"]).abs().max() < 1e-6  # Σ imbalance = NIV
    assert ((ok.up_volume + ok.down_volume) - ok.niv.abs() + ok.unmet_volume).abs().max() < 1e-6
```

  `test_gb_example_perfect_forecast(tmp_path)` copies the scenario folder to `tmp_path` and deletes the four `*_da_df.csv` / `*_id_df.csv` files, so the vintages fall back to the actual series. It runs the scenario and asserts `meta.niv.abs().max() < 1e-6` and `disp.imbalance.abs().max() < 1e-6`.
- [ ] **Step 3:** Run `pytest tests/test_sequential_integration.py -v`. Expected: FAIL on the first run until the config and strategy wiring is right. Fix by following each error to the owning task's code. Don't weaken the assertions.
- [ ] **Step 4:** Run `pytest tests/test_sequential_integration.py -v` and `assume -s example_gb_01 -c base -db "sqlite:///./examples/local_db/assume_db.db"`. Expected: PASS, and the CLI finishes without errors.
- [ ] **Step 5:** Commit: `feat(examples): add GB sequential market example with synthetic forecast vintages`

---

### Task 10: Documentation and final regression

**Files:**
- Create: `docs/source/sequential_markets.rst`. Cover:
  - the market timeline table, including how the `first_delivery` offsets map to D+1;
  - position, FPN, physical and imbalance, with the wind example from the spec;
  - the NIV/BAL clearing rule and the imbalance sign convention;
  - the config keys: `unit_types`, `forecast_vintage`, `imbalance_settlement`, `additional_fields`, and the vintage file names;
  - the strategy names, their `bidding_strategy_params` (including what `bal_markup` does to the imbalance price) and their rules;
  - how to run `gb_sequential`.
- Modify: `docs/source/index.rst` (toctree entry next to `market_config`) and `docs/source/release_notes.rst` (an "Upcoming Release" entry listing the new mechanism, units, strategies, loader options and example).

- [ ] **Step 1:** Write the docs page and the release-notes entry.
- [ ] **Step 2:** Run `pre-commit run --all-files`. Expected: all hooks pass (fix codespell/ruff findings).
- [ ] **Step 3:** Run `pytest -m "not require_learning and not require_network"`. Expected: PASS, with the same pass count as `main` plus the new tests.
- [ ] **Step 4:** Commit: `docs: document GB sequential market setup`

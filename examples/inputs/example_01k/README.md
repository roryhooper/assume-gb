<!--
SPDX-FileCopyrightText: ASSUME Developers

SPDX-License-Identifier: MIT
-->

# example_01k: sequential day-ahead and intraday markets

A small GB-style market over one delivery week (Mon 18 to Sun 24 March 2019) with two sequential energy auctions that both trade the next day's 24 hourly products:

- **DA** (day-ahead): opens 09:00 on the day before delivery and clears at 10:00.
- **ID** (intraday): opens 22:00 and clears at 23:00. It trades *changes* to the positions taken in DA, two hours before delivery begins.

Both are pay-as-clear auctions cleared with `complex_clearing`. The scenario shows how units carry a position from DA into ID, bid against it with their updated forecasts and how profit is accounted per market. It is built from the German 2019 data of `example_03`, scaled so that the hourly demand peak is 1000 MW.

## Units

Wind onshore (692 MW), wind offshore (98 MW), solar (635 MW), three gas units (CCGT, 93 to 94 MW, efficiencies 0.52 to 0.58), one hard coal unit (321 MW), one lignite unit (271 MW), one battery (100 MW charge / 90 MW discharge, 190 MWh) and one inflexible demand unit.

- Renewables bid with `powerplant_energy_naive`, the demand with `demand_energy_naive`, the battery with `storage_energy_heuristic_flexable` and all thermal units with `powerplant_energy_heuristic_flexable_hourly_linked` (see the bidding strategies documentation) in both markets.
- Thermal units use `partial_load_eff: true`. The start-cost thresholds `downtime_hot_start` (8 h) and `downtime_warm_start` (48 h) are set explicitly in `powerplant_units.csv`; their default is 0, which would make every start a cold start.

## Run

```bash
assume -s example_01k -c base -db "sqlite:///./examples/local_db/assume_db.db"
```

or from Python, using the entry `small_sequential_da_id` in `examples/examples.py`:

```bash
python examples/examples.py   # select small_sequential_da_id
```

The study case is `base`. Use `assume-market examples/inputs/example_01k/config.yaml --case-study base` to display the market timing.

## What to look at

- `market_dispatch` and `market_meta`: DA and ID results, one per day.
- `unit_dispatch`: per unit and hour, `power` (physical output), the per-market columns `energy_DA`, `energy_ID`, `energy_cashflow_<m>`, `energy_total_costs_<m>`, `energy_profit_<m>` and `energy_deviation` (physical output minus position).

### Lead day

17 March is a lead day without deliveries: the first auctions open on it for 18 March. Every thermal unit has therefore been off for 24 hours at the first DA auction and prices its first start as a warm start (24 h is within `downtime_warm_start` of 48 h), the same start-up cost the accounting books. This is intended; analysis should exclude 17 March, and the first delivery day carries start-up effects.

### Reading scarcity prices

In this run 27 DA hours and 22 ID hours (of 168 each) clear at the price cap of 3000 EUR/MWh, and 176.7 MWh of demand in 13 hours is not bought at all (realised demand minus the demand's final position). Every one of those 13 hours clears ID at the cap while thermal capacity is idle. The week has enough capacity; these are not physical scarcity events.

The dominant cause is a known limitation of the clearing: a thermal unit's start bid (all-or-nothing block at min load) is judged together with its linked flexible bid. When the flexible bid sets the hourly price at its average cost of the range above min load, the pair has a negative surplus, and `complex_clearing` removes the block and re-solves. The removal is permanent: the unit is not re-admitted even when the remaining supply then cannot meet demand, so the demand is left unbought at the cap.

Forecast errors are a secondary cause: a DA over-forecast of demand or under-forecast of wind or solar can push a DA hour to the cap even though the realised capacity suffices, with ID correcting back down.

Before reading a cap event as physical scarcity, check the ID price, the idle thermal capacity and the forecast error of that hour. Likewise, ID hours with few or no trades have poorly defined prices; flag them instead of reading them literally. The integration test `tests/test_sequential_integration.py` pins the cap-hour counts and the unserved demand.

Note that the demand unit's `energy_deviation` is zero by construction (its physical consumption is taken to be its position), so it does not show unserved demand; compare the realised `demand_df.csv` with the demand's `energy_DA` + `energy_ID` instead.

### Expected deviations

`energy_deviation` is not exactly zero in this example; at the end of the run the `UnitsOperator` logs one summary per affected unit (per-window details at `DEBUG` level). Residual deviations come from:

- thermal units, which decide one hour at a time: minimum operating and down times are enforced at delivery, which can override unsold hours (the hard coal unit shows about 2.7 GWh over the week);
- small corrections for which the ID has no counterparty;
- the battery, whose joint state-of-charge feasibility can be violated under partial acceptance.

Demand and solar have no deviation. The integration test `tests/test_sequential_integration.py` pins bounds for the weekly totals.

## Forecast errors and regenerating the inputs

All input files except `config.yaml` are written by `generate_inputs.py` (seeded, deterministic):

```bash
python examples/inputs/example_01k/generate_inputs.py
```

The realised series are `demand_df.csv`, `availability_df.csv` and `fuel_prices_df.csv`. The DA forecasts `availability_DA_df.csv` and `demand_DA_df.csv` are the realised series plus correlated AR(1) forecast errors. ID has no forecast files, so it falls back to the realised series, i.e. the ID forecast is perfect.

At the top of the script, edit:

- `SEED`: seed of the random number generator.
- `ERRORS["DA"][<series>]`: `sigma` (size of the error, as a fraction of capacity for wind and relative to the realised value for solar and demand) and `phi` (persistence from hour to hour) for `wind_onshore`, `wind_offshore`, `solar` and `demand`.
- `CORRELATION["DA"]`: correlation of the error innovations between the four series. It must be symmetric and positive definite; the script stops with an error message otherwise.
- Adding an `"ID"` entry to both dicts writes `availability_ID_df.csv` and `demand_ID_df.csv`, giving ID its own, imperfect forecast.

The default error sizes and correlations are assumptions based on physical reasoning and are not calibrated to forecast-error data.

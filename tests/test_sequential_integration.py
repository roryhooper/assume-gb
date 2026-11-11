# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT
"""
System gate for the sequential DA/ID market feature (spec section 9.4).

Runs the committed ``example_01k`` scenario once (lead day 17 March, delivery
18-24 March 2019) and checks the cross-market invariants on the outputs.
"""

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine

from assume import World
from assume.scenario.loader_csv import load_scenario_folder

THERMAL_UNITS = ["Gas 1", "Gas 2", "Gas 3", "Hard coal", "Lignite"]
DELIVERY_DAYS = pd.date_range("2019-03-18", "2019-03-24", freq="D")
TOL = 1e-6


@pytest.fixture(scope="module")
def sim(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("sequential") / "test.db"
    uri = f"sqlite:///{db_path}"
    world = World(database_uri=uri)
    load_scenario_folder(world, "examples/inputs", "example_01k", "base")
    world.run()

    engine = create_engine(uri)
    with engine.begin() as conn:
        tables = {
            name: pd.read_sql(f"SELECT * FROM {name}", conn)
            for name in [
                "market_meta",
                "market_orders",
                "unit_dispatch",
                "power_plant_meta",
                "storage_meta",
            ]
        }
    engine.dispose()
    for name, col in [
        ("market_meta", "product_start"),
        ("market_orders", "start_time"),
        ("unit_dispatch", "time"),
    ]:
        tables[name][col] = pd.to_datetime(tables[name][col])
    return world, tables


def _positions(tables) -> pd.DataFrame:
    """Final market position per unit and hour: sum of accepted volumes over all markets."""
    orders = tables["market_orders"]
    return (
        orders.groupby(["unit_id", "start_time"])["accepted_volume"]
        .sum()
        .unstack("unit_id")
        .fillna(0.0)
        .sort_index()
    )


def _units(world) -> dict:
    return {
        uid: unit
        for operator in world.unit_operators.values()
        for uid, unit in operator.units.items()
    }


@pytest.mark.slow
def test_sequential_week_runs(sim):
    """DA and ID each clear once per delivery day with 24 hourly products, incl. the last day."""
    _, tables = sim
    meta = tables["market_meta"]
    for market in ["DA", "ID"]:
        m = meta[meta["market_id"] == market]
        assert len(m) == 7 * 24, market
        assert m["product_start"].nunique() == 7 * 24, market
        per_day = m.groupby(m["product_start"].dt.normalize()).size()
        assert list(per_day.index) == list(DELIVERY_DAYS), market
        assert (per_day == 24).all(), market

    # the final delivery day is exported (on_stop flush, spec 5.1)
    dispatch = tables["unit_dispatch"]
    assert (dispatch["time"].dt.normalize() == pd.Timestamp("2019-03-24")).any()
    assert dispatch["time"].max() == pd.Timestamp("2019-03-24 23:00")


@pytest.mark.slow
def test_no_thermal_position_between_zero_and_min_power(sim):
    _, tables = sim
    positions = _positions(tables)
    min_power = tables["power_plant_meta"].set_index("index")["min_power"]
    for unit in THERMAL_UNITS:
        pos = positions[unit]
        bad = pos[(pos > TOL) & (pos < min_power[unit] - TOL)]
        assert bad.empty, f"{unit} has positions below min_power:\n{bad}"


# Residual deviations (physical dispatch minus market position) are designed behaviour:
#  - spec 5.5: ID correction bids that find no counterparty (wind, divisible volume
#    needed while the only ID sellers are thermal block bids);
#  - spec L1: min up/down time and ramps are enforced over unsold hours on execution;
#  - spec L5: storage bids across hours are not jointly SOC-feasible.
# The totals below are the sum of |energy_deviation| over delivery days (MWh) observed
# in this deterministic run. They are a regression guard: a bug inflating deviations
# must trip them (5 % headroom for float/solver noise).
DEVIATION_SNAPSHOT = {
    "Battery": 812.85,
    "Gas 1": 900.23,
    "Gas 2": 376.94,
    "Gas 3": 334.80,
    "Hard coal": 2697.39,
    "Lignite": 0.0,
    "Solar": 0.0,
    "Wind Offshore": 61.47,
    "Wind Onshore": 240.95,
    "demand_DE": 0.0,
}


@pytest.mark.slow
def test_deviations_are_bounded(sim):
    world, tables = sim
    dispatch = tables["unit_dispatch"]
    delivery = dispatch[dispatch["time"] >= DELIVERY_DAYS[0]]
    max_power = {}
    for name, unit in _units(world).items():
        max_power[name] = max(
            abs(getattr(unit, "max_power", 0.0) or 0.0),
            abs(getattr(unit, "max_power_charge", 0.0) or 0.0),
            abs(getattr(unit, "max_power_discharge", 0.0) or 0.0),
        )

    # Solar's realisation is certain and its corrections always clear. For the demand
    # the zero deviation holds by construction (Demand.execute_current_dispatch takes
    # the position as its physical consumption), so this is NOT a served-load check:
    # see test_cap_hours_and_unserved_demand for the demand that was not bought.
    for name in ["demand_DE", "Solar"]:
        dev = delivery.loc[delivery["unit"] == name, "energy_deviation"]
        assert dev.abs().max() < TOL, name

    for name, group in delivery.groupby("unit"):
        dev = group["energy_deviation"].abs()
        assert dev.max() <= max_power[name] + TOL, name
        assert dev.sum() <= DEVIATION_SNAPSHOT[name] * 1.05 + TOL, (
            f"{name}: total |deviation| {dev.sum():.2f} exceeds snapshot "
            f"{DEVIATION_SNAPSHOT[name]}"
        )


# Known limitation (spec L6): when a start-block family of a thermal unit (start S
# with its linked flexible F) clears with F at the chord price setting the hourly
# price, the family's surplus is negative, and complex_clearing's surplus loop
# removes the block and re-solves. The removal is permanent, so the unit is not
# re-admitted even when the remaining supply cannot meet demand: idle thermal
# capacity then leaves demand unbought at the 3000 EUR/MWh cap. This, not forecast
# error, is the dominant cause of the cap hours below (every unserved hour clears ID
# at the cap). The values are exact snapshots of this deterministic run.
CAP_PRICE = 3000
CAP_HOURS_SNAPSHOT = {"DA": 27, "ID": 22}
UNSERVED_MWH_SNAPSHOT = 176.67
UNSERVED_HOURS_SNAPSHOT = 13


@pytest.mark.slow
def test_cap_hours_and_unserved_demand(sim):
    _, tables = sim
    meta = tables["market_meta"]
    for market, expected in CAP_HOURS_SNAPSHOT.items():
        prices = meta.loc[meta["market_id"] == market, "price"]
        assert int((prices >= CAP_PRICE - TOL).sum()) == expected, market

    # unserved = realised demand - final demand position (both markets)
    realised = pd.read_csv(
        "examples/inputs/example_01k/demand_df.csv", index_col=0, parse_dates=True
    )["demand_DE"].abs()
    realised = realised[realised.index >= DELIVERY_DAYS[0]]
    bought = -_positions(tables)["demand_DE"].reindex(realised.index, fill_value=0.0)
    unserved = realised - bought
    assert unserved.min() >= -TOL, "demand bought more than its realised load"
    assert unserved.sum() == pytest.approx(UNSERVED_MWH_SNAPSHOT, abs=0.01)
    assert int((unserved > TOL).sum()) == UNSERVED_HOURS_SNAPSHOT
    # every unserved hour clears ID at the cap
    id_prices = meta[meta["market_id"] == "ID"].set_index("product_start")["price"]
    assert (id_prices.reindex(unserved[unserved > TOL].index) >= CAP_PRICE - TOL).all()


@pytest.mark.slow
def test_profit_attribution_reconciles(sim):
    """Per unit: sum of per-market profits == total cashflow - cost of the final position."""
    world, tables = sim
    dispatch = tables["unit_dispatch"]
    positions = _positions(tables)
    units = _units(world)
    for name, unit in units.items():
        d = dispatch[dispatch["unit"] == name]
        profit = (
            d[["energy_profit_DA", "energy_profit_ID"]].fillna(0.0).to_numpy().sum()
        )
        cashflow = d["energy_cashflow"].fillna(0.0).sum()

        # cost of the final position (execution overwrites outputs["energy"])
        idx = unit.index
        pos = positions[name].reindex(idx.as_datetimeindex(), fill_value=0.0)
        unit.outputs["energy"].loc[idx[0] : idx[-1]] = pos.to_numpy()
        cost = float(np.sum(unit.calculate_operating_cost(idx[0], idx[-1])))

        assert profit == pytest.approx(cashflow - cost, rel=1e-4, abs=1e-2), name


@pytest.mark.slow
def test_per_market_volumes_exported(sim):
    """unit_dispatch carries energy_DA and energy_ID; they add up to the final position."""
    _, tables = sim
    dispatch = tables["unit_dispatch"]
    assert {"energy_DA", "energy_ID"} <= set(dispatch.columns)
    positions = _positions(tables)
    exported = (
        dispatch.set_index(["time", "unit"])[["energy_DA", "energy_ID"]]
        .fillna(0.0)
        .sum(axis=1)
        .unstack("unit")
        .reindex(index=positions.index, columns=positions.columns)
        .fillna(0.0)
    )
    np.testing.assert_allclose(exported.to_numpy(), positions.to_numpy(), atol=1e-6)


@pytest.mark.slow
def test_battery_soc_within_bounds(sim):
    _, tables = sim
    soc = tables["unit_dispatch"].query("unit == 'Battery'")["soc"].dropna()
    assert len(soc) > 0
    assert soc.min() >= -TOL
    assert soc.max() <= 1 + TOL

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


@pytest.mark.slow
def test_positions_are_deliverable(sim):
    """Option A: the ID forecast equals the realisation, so no deviation arises."""
    _, tables = sim
    dispatch = tables["unit_dispatch"]
    delivery = dispatch[dispatch["time"] >= DELIVERY_DAYS[0]]
    worst = delivery.loc[delivery["energy_deviation"].abs().idxmax()]
    assert delivery["energy_deviation"].abs().max() < TOL, (
        f"max deviation {worst['energy_deviation']} at {worst['unit']} {worst['time']}"
    )


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
def test_battery_soc_within_bounds(sim):
    _, tables = sim
    soc = tables["unit_dispatch"].query("unit == 'Battery'")["soc"].dropna()
    assert len(soc) > 0
    assert soc.min() >= -TOL
    assert soc.max() <= 1 + TOL

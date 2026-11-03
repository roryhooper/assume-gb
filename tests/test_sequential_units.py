# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

import pandas as pd
import pytest

from assume.common.forecaster import PowerplantForecaster, UnitForecaster
from assume.common.market_objects import MarketConfig
from assume.strategies.flexable_storage import StorageEnergyHeuristicFlexableStrategy
from assume.strategies.naive_strategies import EnergyNaiveStrategy
from assume.units.sequential_units import SequentialPowerPlant, SequentialStorage

T = datetime(2022, 1, 1, 5)
H = timedelta(hours=1)


@pytest.fixture
def plant() -> SequentialPowerPlant:
    index = pd.date_range("2022-01-01", periods=24, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=0.5,
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    return SequentialPowerPlant(
        id="seq_pp",
        unit_operator="op",
        technology="hard coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=0,
        efficiency=0.5,
        additional_cost=10,
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
    )


@pytest.fixture
def storage() -> SequentialStorage:
    index = pd.date_range("2022-01-01", periods=24, freq="h")
    forecaster = UnitForecaster(index, availability=1, market_prices={"EOM": 50})
    return SequentialStorage(
        id="seq_st",
        unit_operator="op",
        technology="battery",
        bidding_strategies={"EOM": StorageEnergyHeuristicFlexableStrategy()},
        forecaster=forecaster,
        max_power_charge=-100,
        max_power_discharge=100,
        capacity=1000,
        efficiency_charge=0.9,
        efficiency_discharge=0.95,
        initial_soc=0.5,
    )


def order(market_id, price, vol, start=T):
    return {
        "start_time": start,
        "end_time": start + H,
        "accepted_price": price,
        "accepted_volume": vol,
        "market_id": market_id,
    }


def mc(product_type, market_id="DA"):
    return MarketConfig(market_id=market_id, product_type=product_type)


def test_per_market_cashflow_split(plant):
    da, idm = order("DA", 40, 100), order("ID", 60, -20)
    plant.set_dispatch_plan(mc("energy", "DA"), [da])
    plant.calculate_cashflow("energy", [da])
    plant.set_dispatch_plan(mc("energy", "ID"), [idm])
    plant.calculate_cashflow("energy", [idm])
    assert plant.outputs["energy_cashflow"].at[T] == 2800
    assert plant.outputs["cashflow_DA"].at[T] == 4000
    assert plant.outputs["cashflow_ID"].at[T] == -1200


def test_execute_does_not_clip_position(plant):
    plant.outputs["energy"].at[T] = 900
    plant.outputs["physical"].at[T] = 500
    res = plant.execute_current_dispatch(T, T)
    assert list(res) == [500]
    assert plant.outputs["energy"].at[T] == 900


def test_settle_short_pays(plant):
    plant.outputs["energy"].at[T] = 100
    plant.settle(T, fpn=80, accepted_balancing=0, price=200)
    assert plant.outputs["imbalance"].at[T] == -20
    assert plant.outputs["imbalance_cashflow"].at[T] == -4000
    assert plant.outputs["physical"].at[T] == 80
    assert plant.outputs["fpn"].at[T] == 80


def test_settle_provider_not_penalised(plant):
    plant.outputs["energy"].at[T] = 100
    bal = order("BAL", 150, 30)
    plant.set_dispatch_plan(mc("balancing_energy", "BAL"), [bal])
    plant.settle(T, fpn=100, accepted_balancing=30, price=150)
    assert plant.outputs["physical"].at[T] == 130
    assert plant.outputs["imbalance"].at[T] == 0
    assert plant.outputs["imbalance_cashflow"].at[T] == 0
    assert plant.net_position(T) == 130


def test_settle_profit_sums_components(plant):
    da = order("DA", 40, 100)
    plant.set_dispatch_plan(mc("energy", "DA"), [da])
    plant.calculate_cashflow("energy", [da])
    bal = order("BAL", 150, 30)
    plant.set_dispatch_plan(mc("balancing_energy", "BAL"), [bal])
    plant.calculate_cashflow("balancing_energy", [bal])
    plant.settle(T, fpn=100, accepted_balancing=10, price=200)
    o = plant.outputs
    assert o["imbalance"].at[T] == -20
    assert o["generation_costs"].at[T] > 0
    # base-unit/dashboard name carries the same costs, subtracted only once
    assert o["energy_generation_costs"].at[T] == o["generation_costs"].at[T]
    expected = (
        4000
        + o["balancing_energy_cashflow"].at[T]
        + o["imbalance_cashflow"].at[T]
        - o["generation_costs"].at[T]
    )
    assert o["balancing_energy_cashflow"].at[T] == 4500
    assert o["profit"].at[T] == pytest.approx(expected)
    # idempotent
    plant.settle(T, fpn=100, accepted_balancing=10, price=200)
    assert o["profit"].at[T] == pytest.approx(expected)


def test_calculate_generation_cost_is_noop(plant):
    plant.outputs["physical"].at[T] = 100
    plant.calculate_generation_cost(T, T, "energy")
    assert plant.outputs["generation_costs"].at[T] == 0


def test_storage_soc_follows_physical(storage):
    t = T
    soc0 = storage.outputs["soc"].at[t]
    storage.set_dispatch_plan(mc("energy"), [order("DA", 40, 50)])
    assert storage.outputs["soc"].at[t + H] == soc0
    assert storage.outputs["energy"].at[t] == 50
    storage.settle(t, fpn=50, accepted_balancing=0, price=0)
    expected = soc0 - 50 / storage.efficiency_discharge / storage.capacity
    assert storage.outputs["soc"].at[t + H] == pytest.approx(expected)
    assert storage.outputs["energy"].at[t] == 50


def test_storage_charge_soc(storage):
    t = T
    soc0 = storage.outputs["soc"].at[t]
    storage.settle(t, fpn=-50, accepted_balancing=0, price=0)
    expected = soc0 + 50 * storage.efficiency_charge / storage.capacity
    assert storage.outputs["soc"].at[t + H] == pytest.approx(expected)

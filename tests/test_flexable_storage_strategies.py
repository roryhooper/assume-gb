# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from assume.common.forecaster import UnitForecaster
from assume.strategies import (
    StorageCapacityHeuristicBalancingNegStrategy,
    StorageCapacityHeuristicBalancingPosStrategy,
    StorageEnergyHeuristicFlexableStrategy,
)
from assume.units import Storage


@pytest.fixture
def storage() -> Storage:
    # Create a PowerPlant instance with some example parameters
    index = pd.date_range("2023-07-01", periods=48, freq="h")
    forecaster = UnitForecaster(index, market_prices={"EOM": 50})
    return Storage(
        id="Test_Storage",
        unit_operator="TestOperator",
        technology="TestTechnology",
        bidding_strategies={},
        forecaster=forecaster,
        max_power_charge=-100,
        max_power_discharge=100,
        capacity=1000,
        initial_soc=0.5,
        efficiency_charge=0.9,
        efficiency_discharge=0.95,
        ramp_down_charge=-50,
        ramp_down_discharge=50,
        ramp_up_charge=-60,
        ramp_up_discharge=60,
        additional_cost_charge=3,
        additional_cost_discharge=4,
        additional_cost=1,
    )


def test_flexable_eom_storage(mock_market_config, storage):
    index = pd.date_range("2023-07-01", periods=4, freq="h")
    start = datetime(2023, 7, 1)
    end = datetime(2023, 7, 1, 1)
    strategy = StorageEnergyHeuristicFlexableStrategy()
    mc = mock_market_config
    product_tuples = [(start, end, None)]

    # constant price of 50
    storage.forecaster = UnitForecaster(index, market_prices={"EOM": 50})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    # no change in price forecast -> no bidding
    assert len(bids) == 1
    assert bids[0]["price"] == 50 / storage.efficiency_discharge
    assert bids[0]["volume"] == 60

    # increase the current price forecast -> discharging
    storage.forecaster = UnitForecaster(index, market_prices={"EOM": [60, 50, 50, 50]})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 52.5 / storage.efficiency_discharge
    assert bids[0]["volume"] == 60

    # decrease current price forecast -> charging
    storage.forecaster = UnitForecaster(index, market_prices={"EOM": [40, 50, 50, 50]})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 47.5 * storage.efficiency_charge
    assert bids[0]["volume"] == -60

    # change to dam bidding
    day = pd.date_range(start, start + timedelta(hours=23), freq="h")
    index = pd.date_range("2023-07-01", periods=24, freq="h")
    product_tuples = [(start, start + timedelta(hours=1), None) for start in day]
    storage.foresight = pd.Timedelta(hours=4)
    forecast = [
        20,
        50,
        50,
        50,
        80,
        50,
        50,
        50,
        80,
        50,
        50,
        50,
        80,
        50,
        50,
        50,
        20,
        50,
        50,
        50,
        20,
        50,
        50,
        50,
    ]
    storage.forecaster = UnitForecaster(index, market_prices={"EOM": forecast})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 24
    assert math.isclose(
        bids[0]["price"],
        np.mean(forecast[0:13]) * storage.efficiency_charge,
        abs_tol=0.01,
    )
    assert bids[0]["volume"] == -60
    assert math.isclose(
        bids[1]["price"],
        np.mean(forecast[0:14]) * storage.efficiency_charge,
        abs_tol=0.01,
    )
    assert bids[1]["volume"] == -100
    assert math.isclose(
        bids[2]["price"],
        np.mean(forecast[0:15]) * storage.efficiency_charge,
        abs_tol=0.01,
    )
    assert bids[2]["volume"] == -100
    assert math.isclose(
        bids[3]["price"],
        np.mean(forecast[0:16]) * storage.efficiency_charge,
        abs_tol=0.01,
    )
    assert bids[3]["volume"] == -100
    assert math.isclose(
        bids[4]["price"],
        np.mean(forecast[0:17]) / storage.efficiency_discharge,
        abs_tol=0.01,
    )
    assert math.isclose(bids[4]["volume"], 60, abs_tol=0.01)
    assert math.isclose(
        bids[14]["price"],
        np.mean(forecast[2:]) * storage.efficiency_charge,
        abs_tol=0.01,
    )
    assert bids[14]["volume"] == -100
    assert math.isclose(
        bids[20]["price"],
        np.mean(forecast[6:]) * storage.efficiency_charge,
        abs_tol=0.01,
    )
    assert bids[20]["volume"] == -60


def test_flexable_pos_crm_storage(mock_market_config, storage):
    index = pd.date_range("2023-07-01", periods=4, freq="h")
    start = datetime(2023, 7, 1)
    end = datetime(2023, 7, 1, 4, 0, 0)
    strategy = StorageCapacityHeuristicBalancingPosStrategy()
    mc = mock_market_config
    mc.product_type = "energy_pos"
    product_tuples = [(start, end, None)]

    # constant price of 50
    specific_revenue = (50 - 4) * 360 / (0.36 * 1000)

    storage.forecaster = UnitForecaster(index, market_prices={"EOM": 50})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert math.isclose(bids[0]["price"], specific_revenue)
    assert bids[0]["volume"] == 60

    # assert capacity_pos
    mc.product_type = "capacity_pos"
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert math.isclose(bids[0]["price"], specific_revenue)
    assert bids[0]["volume"] == 60

    # specific revenue < 0
    storage.forecaster = UnitForecaster(index, market_prices={"EOM": 3})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 0
    assert bids[0]["volume"] == 60

    # was charging before
    storage.outputs["energy"][start] = -60
    product_tuples = [
        (start + pd.Timedelta(hours=1), end + pd.Timedelta(hours=1), None)
    ]
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1


def test_flexable_neg_crm_storage(mock_market_config, storage):
    index = pd.date_range("2023-07-01", periods=4, freq="h")
    start = datetime(2023, 7, 1)
    end = datetime(2023, 7, 1, 4, 0, 0)
    strategy = StorageCapacityHeuristicBalancingNegStrategy()
    mc = mock_market_config
    # Calculations for negative energy
    mc.product_type = "energy_neg"
    product_tuples = [(start, end, None)]

    # constant price of 50
    storage.forecaster = UnitForecaster(index, market_prices={"EOM": 50})
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert math.isclose(bids[0]["price"], 0)
    assert bids[0]["volume"] == 60

    # assert capacity_pos
    mc.product_type = "capacity_neg"
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert math.isclose(bids[0]["price"], 0)
    assert bids[0]["volume"] == 60

    # was charging before
    storage.outputs["energy"][start] = 60
    product_tuples = [
        (start + pd.Timedelta(hours=1), end + pd.Timedelta(hours=1), None)
    ]
    bids = strategy.calculate_bids(storage, mc, product_tuples=product_tuples)
    assert len(bids) == 1


if __name__ == "__main__":
    # run pytest and enable prints
    import pytest

    pytest.main(["-s", __file__])


def test_flexable_storage_reverses_day_ahead_discharge_in_intraday():
    """Spec 6.6: the ranges are relative to the position, so a DA discharge can be
    bought back (and turned into charging) in ID when the ID forecast is low."""
    from assume.common.market_objects import MarketConfig

    index = pd.date_range("2023-07-01", periods=24, freq="h")
    da_prices = [50.0] * 24
    id_prices = [50.0] * 24
    da_prices[10] = 80.0  # DA expects a peak -> discharge
    id_prices[10] = 20.0  # ID expects a low price -> charge
    forecaster = UnitForecaster(index, market_prices={"DA": da_prices, "ID": id_prices})
    strategy = StorageEnergyHeuristicFlexableStrategy()
    storage = Storage(
        id="Battery",
        unit_operator="TestOperator",
        technology="li-ion_battery",
        bidding_strategies={"DA": strategy, "ID": strategy},
        forecaster=forecaster,
        max_power_charge=-100,
        max_power_discharge=90,
        capacity=190,
        initial_soc=0.5,
        efficiency_charge=0.95,
        efficiency_discharge=0.95,
    )
    t = index[10]
    products = [(t, t + timedelta(hours=1), None)]

    da = MarketConfig(market_id="DA", product_type="energy")
    (da_bid,) = strategy.calculate_bids(storage, da, products)
    assert da_bid["volume"] == pytest.approx(90)
    storage.set_dispatch_plan(
        da, [{**da_bid, "accepted_volume": 90, "accepted_price": 80, "market_id": "DA"}]
    )
    assert storage.outputs["energy"].at[t] == pytest.approx(90)

    id_ = MarketConfig(market_id="ID", product_type="energy")
    (id_bid,) = strategy.calculate_bids(storage, id_, products)
    # a buy that undoes the whole DA discharge and charges on top of it
    assert id_bid["volume"] < -90
    assert id_bid["price"] < 80
    storage.set_dispatch_plan(
        id_,
        [
            {
                **id_bid,
                "accepted_volume": id_bid["volume"],
                "accepted_price": 20,
                "market_id": "ID",
            }
        ],
    )
    position = storage.outputs["energy"].at[t]
    assert position < 0  # net charging
    assert position >= storage.max_power_charge
    assert storage.outputs["energy_DA"].at[t] == pytest.approx(90)
    assert storage.outputs["energy_ID"].at[t] == pytest.approx(id_bid["volume"])

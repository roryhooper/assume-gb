# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import math
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from assume.common.forecaster import PowerplantForecaster
from assume.strategies import (
    CapacityHeuristicBalancingNegStrategy,
    CapacityHeuristicBalancingPosStrategy,
    EnergyHeuristicFlexableStrategy,
)
from assume.units import PowerPlant

start = datetime(2023, 7, 1)
end = datetime(2023, 7, 2)


@pytest.fixture
def power_plant() -> PowerPlant:
    # Create a PowerPlant instance with some example parameters
    index = pd.date_range("2023-07-01", periods=48, freq="h")
    forecaster = PowerplantForecaster(
        index,
        fuel_prices={"lignite": 10, "co2": 10},
    )
    return PowerPlant(
        id="test_pp",
        unit_operator="test_operator",
        technology="hard coal",
        index=forecaster.index,
        max_power=1000,
        min_power=200,
        efficiency=0.5,
        additional_cost=10,
        bidding_strategies={},
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
    )


def test_flexable_eom(mock_market_config, power_plant):
    end = datetime(2023, 7, 1, 1)
    strategy = EnergyHeuristicFlexableStrategy()
    mc = mock_market_config
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 2
    assert bids[0]["price"] == 40
    assert bids[0]["volume"] == 200
    assert bids[1]["price"] == 40
    assert bids[1]["volume"] == 800

    # start-up situation with ramping restriction
    power_plant.ramp_up = 400
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 2
    assert bids[0]["price"] == 40
    assert bids[0]["volume"] == 200
    assert bids[1]["price"] == 40
    assert bids[1]["volume"] == 200

    # CHP with ramping restrictions, but without start-up
    power_plant.outputs["heat"][start] = 300
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 2
    assert math.isclose(bids[0]["price"], (40 - 10 / 0.9))
    assert bids[0]["volume"] == 300
    assert bids[1]["price"] == 40
    assert bids[1]["volume"] == 100


def test_flexable_pos_reserve(mock_market_config, power_plant):
    end = datetime(2023, 7, 1, 4, 0, 0)
    strategy = CapacityHeuristicBalancingPosStrategy()
    mc = mock_market_config
    mc.product_type = "energy_pos"
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 40
    assert bids[0]["volume"] == 1000

    mc.product_type = "capacity_pos"
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 50
    assert bids[0]["volume"] == 1000

    # increase the marginal cost to ensure specific_revenue < 0
    power_plant.marginal_cost[start] = 60
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 10
    assert bids[0]["volume"] == 1000


def test_flexable_neg_reserve(mock_market_config, power_plant):
    end = datetime(2023, 7, 1, 4, 0, 0)
    strategy = CapacityHeuristicBalancingNegStrategy()
    mc = mock_market_config
    # Calculations for negative energy
    mc.product_type = "energy_neg"
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert bids == []

    # change previous power to max_power/2 to enable ramp down
    power_plant.outputs["energy"][start] = power_plant.max_power / 2
    product_tuples = [
        (start + pd.Timedelta(hours=1), end + pd.Timedelta(hours=1), None)
    ]

    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == -40
    assert bids[0]["volume"] == 300

    # Calculations for negative capacity
    mc.product_type = "capacity_neg"
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert bids == []

    # change previous power to max_power/2 to enable ramp down
    power_plant.outputs["energy"][start] = power_plant.max_power / 2
    product_tuples = [
        (start + pd.Timedelta(hours=1), end + pd.Timedelta(hours=1), None)
    ]

    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert bids[0]["price"] == 0
    assert bids[0]["volume"] == 300

    # increase the marginal cost to ensure specific_revenue < 0
    power_plant.marginal_cost[start + pd.Timedelta(hours=1)] = 60

    bids = strategy.calculate_bids(power_plant, mc, product_tuples=product_tuples)
    assert len(bids) == 1
    assert math.isclose(bids[0]["price"], 50 / 3)
    assert bids[0]["volume"] == 300


def test_flexable_no_overcommitment_with_position(mock_market_config):
    index = pd.date_range("2023-07-01", periods=48, freq="h")
    forecaster = PowerplantForecaster(index, fuel_prices={"lignite": 10, "co2": 10})
    pp = PowerPlant(
        id="test_pp",
        unit_operator="test_operator",
        technology="hard coal",
        index=forecaster.index,
        max_power=500,
        min_power=100,
        efficiency=0.5,
        additional_cost=10,
        bidding_strategies={},
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
    )
    pp.outputs["energy"][start] = 300  # position: additional min = -200, max = 200
    # market-aware bounds relative to the position may be negative (capacity to reduce)
    pp.calculate_min_max_power = lambda *a, **k: (np.array([-200.0]), np.array([200.0]))
    strategy = EnergyHeuristicFlexableStrategy()
    bids = strategy.calculate_bids(
        pp, mock_market_config, product_tuples=[(start, datetime(2023, 7, 1, 1), None)]
    )
    assert all(b["volume"] >= 0 for b in bids)
    assert sum(b["volume"] for b in bids) <= 500 - 300


def test_flexable_profit_accumulates_across_markets(power_plant):
    from assume.common.market_objects import MarketConfig

    t_end = datetime(2023, 7, 1, 1)
    power_plant.outputs["energy"][start] = 100
    strategy = EnergyHeuristicFlexableStrategy()
    mc = MarketConfig(market_id="DA", product_type="energy")
    cost = power_plant.calculate_marginal_cost(start, 100) * 100
    for mid, price, vol in [("DA", 50, 60), ("ID", 70, 40)]:
        ob = [
            {
                "start_time": start,
                "end_time": t_end,
                "accepted_price": price,
                "accepted_volume": vol,
                "market_id": mid,
            }
        ]
        power_plant.calculate_cashflow("energy", ob)
        strategy.calculate_reward(power_plant, mc, ob)
    expected = 50 * 60 + 70 * 40 - cost
    assert math.isclose(power_plant.outputs["profit"].at[start], expected)


if __name__ == "__main__":
    # run pytest and enable prints
    import pytest

    pytest.main(["-s", __file__])

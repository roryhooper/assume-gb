# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

import pandas as pd
import pytest

from assume.common.forecaster import DemandForecaster, PowerplantForecaster
from assume.common.market_objects import MarketConfig
from assume.strategies import bidding_strategies
from assume.strategies.sequential_strategies import (
    DemandSequentialFixedStrategy,
    RenewableSequentialHeuristicStrategy,
)
from assume.units.sequential_units import SequentialDemand, SequentialPowerPlant

T = datetime(2022, 1, 1, 5)
H = timedelta(hours=1)
INDEX = pd.date_range("2022-01-01", periods=24, freq="h")


def market(market_id, product_type, vintage):
    return MarketConfig(
        market_id=market_id,
        product_type=product_type,
        maximum_bid_price=3000,
        param_dict={"forecast_vintage": vintage},
        additional_fields=["fpn", "position"] if market_id == "BAL" else [],
    )


DA = market("DA", "energy", "da")
ID = market("ID", "energy", "id")
BAL = market("BAL", "balancing_energy", "actual")


def products(n=1, start=T):
    return [(start + i * H, start + (i + 1) * H, None) for i in range(n)]


@pytest.fixture
def wind():
    forecaster = PowerplantForecaster(
        INDEX,
        availability=0.5,
        availability_vintages={"da": 0.8, "id": 0.6},
        fuel_prices={},
        market_prices={},
    )
    strategy = RenewableSequentialHeuristicStrategy(bal_markup=10, unknown=1)
    return SequentialPowerPlant(
        id="wind",
        unit_operator="op",
        technology="wind",
        bidding_strategies={"DA": strategy, "ID": strategy, "BAL": strategy},
        index=forecaster.index,
        max_power=100,
        min_power=0,
        efficiency=1,
        additional_cost=2,
        fuel_type="renewable",
        emission_factor=0,
        forecaster=forecaster,
    )


@pytest.fixture
def demand():
    forecaster = DemandForecaster(
        INDEX, demand=-80, demand_vintages={"da": -100, "id": -90}
    )
    strategy = DemandSequentialFixedStrategy()
    return SequentialDemand(
        id="dem",
        unit_operator="op",
        technology="demand",
        bidding_strategies={"DA": strategy, "ID": strategy, "BAL": strategy},
        forecaster=forecaster,
        max_power=-1000,
        min_power=0,
        price=3000,
    )


def test_renewable_da_uses_da_vintage(wind):
    (bid,) = wind.calculate_bids(DA, products())
    assert bid["volume"] == pytest.approx(80)
    assert bid["price"] == pytest.approx(wind.calculate_marginal_cost(T, 0))
    assert bid["node"] == wind.node


def test_renewable_id_buys_back_at_cap(wind):
    wind.outputs["energy"].at[T] = 80
    (bid,) = wind.calculate_bids(ID, products())
    assert bid["volume"] == pytest.approx(-20)
    assert bid["price"] == 3000


def test_renewable_balancing_fpn_and_dec(wind):
    wind.outputs["energy"].at[T] = 80
    bids = wind.calculate_bids(BAL, products())
    mc = wind.calculate_marginal_cost(T, 0)
    assert all(b["fpn"] == 50 and b["position"] == 80 for b in bids)
    assert len(bids) == 1
    assert bids[0]["volume"] == pytest.approx(-50)
    assert bids[0]["price"] == pytest.approx(mc - 10)


def test_renewable_balancing_notification_when_no_output(wind):
    wind.forecaster.availability[:] = 0
    bids = wind.calculate_bids(BAL, products())
    assert len(bids) == 1
    assert bids[0]["volume"] == 0 and bids[0]["fpn"] == 0


def test_demand_fixed_price_and_sellback(demand):
    (bid,) = demand.calculate_bids(DA, products())
    assert bid["volume"] == -100 and bid["price"] == 3000
    demand.outputs["energy"].at[T] = -100
    (bid,) = demand.calculate_bids(ID, products())
    assert bid["volume"] == 10 and bid["price"] == 0


def test_demand_balancing_notification_only(demand):
    demand.outputs["energy"].at[T] = -90
    bids = demand.calculate_bids(BAL, products())
    assert len(bids) == 1
    assert bids[0]["volume"] == 0
    assert bids[0]["fpn"] == -80
    assert bids[0]["position"] == -90


def test_realised_price_recorded_from_rejected_order(wind):
    strategy = wind.bidding_strategies["DA"]
    order = {
        "start_time": T,
        "end_time": T + H,
        "accepted_volume": 0,
        "accepted_price": 55,
    }
    strategy.calculate_reward(wind, DA, [order])
    assert strategy.realised_price("DA", T) == 55
    assert strategy.realised_price("DA", T + H) is None
    assert strategy.realised_price("ID", T) is None
    assert "profit" not in wind.outputs


def test_one_bid_per_product(wind):
    prods = products(24, datetime(2022, 1, 1))
    bids = wind.calculate_bids(DA, prods)
    assert len(bids) == 24
    for bid, prod in zip(bids, prods):
        assert bid["start_time"] == prod[0]
        assert bid["end_time"] == prod[1]


def test_registered():
    assert (
        bidding_strategies["demand_sequential_fixed"] is DemandSequentialFixedStrategy
    )
    assert (
        bidding_strategies["renewable_sequential_heuristic"]
        is RenewableSequentialHeuristicStrategy
    )

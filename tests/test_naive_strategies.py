# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from dateutil import rrule as rr
from dateutil.relativedelta import relativedelta as rd

from assume.common.base import BaseStrategy
from assume.common.forecaster import (
    DemandForecaster,
    PowerplantForecaster,
    UnitForecaster,
)
from assume.common.market_objects import MarketConfig, MarketProduct
from assume.common.utils import get_available_products
from assume.markets.base_market import MarketRole
from assume.strategies import (
    EnergyHeuristicElasticStrategy,
    EnergyNaiveProfileStrategy,
    EnergyNaiveStrategy,
)
from assume.strategies.naive_strategies import (
    DemandEnergyNaiveStrategy,
    EnergyNaiveRedispatchStrategy,
)
from assume.units.demand import Demand
from assume.units.powerplant import PowerPlant
from tests.conftest import MockMinMaxUnit

start = datetime(2023, 7, 1)
end = datetime(2023, 7, 2)


def test_naive_strategy(mock_market_config, mock_supports_minmax):
    strategy = EnergyNaiveStrategy()
    mc = mock_market_config
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(
        mock_supports_minmax, mc, product_tuples=product_tuples
    )
    assert bids[0]["price"] == 3
    assert bids[0]["volume"] == 400


def test_naive_da_strategy(mock_market_config, mock_supports_minmax):
    # test with mock market
    strategy = EnergyNaiveProfileStrategy()
    mc = mock_market_config
    unit = mock_supports_minmax
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(unit, mc, product_tuples=product_tuples)
    assert bids[0]["price"] == 3
    assert bids[0]["volume"] == {start: 400}

    # test with dam
    mc.market_products = [MarketProduct(rd(hours=+1), 24, rd(hours=24))]
    next_opening = start
    products = get_available_products(mc.market_products, next_opening)
    assert len(products) == 24

    bids = strategy.calculate_bids(unit, mc, product_tuples=products)
    assert bids[0]["price"] == 3
    assert len(bids[0]["volume"]) == 24
    assert bids[0]["volume"][products[0][0]] == 400
    assert bids[0]["volume"][products[-1][0]] == 400


class MockElasticDemand:
    """
    Minimal mock object to simulate an elastic demand unit.
    """

    def __init__(
        self,
        elasticity=-0.1,
        max_price=100.0,
        num_bids=4,
        max_power=500,
        min_power=0,
        node="nodeX",
        elasticity_model="isoelastic",
    ):
        self.elasticity = elasticity
        self.max_price = max_price
        self.min_price = 0.0
        self.elasticity_model = elasticity_model
        self.num_bids = num_bids
        self.max_power = max_power
        self.min_power = min_power
        self.node = node


@pytest.mark.parametrize(
    "elasticity, model",
    [
        (-0.1, "isoelastic"),
        (-0.5, "isoelastic"),
        (-0.1, "linear"),
        (-0.5, "linear"),
    ],
)
def test_demand_energy_heuristic_elastic_strategy_robust_range(
    mock_market_config, elasticity, model
):
    """
    Parametric test to check multiple elasticity values and model types.
    Ensures bid count, price monotonicity, and volume validity.
    """
    strategy = EnergyHeuristicElasticStrategy()
    unit = MockElasticDemand(
        elasticity=elasticity,
        max_price=100.0,
        num_bids=4,
        max_power=500,
        min_power=0,
        elasticity_model=model,
    )
    product_tuples = [(start, end, None)]
    bids = strategy.calculate_bids(unit, mock_market_config, product_tuples)

    assert len(bids) == unit.num_bids
    prices = [bid["price"] for bid in bids]
    volumes = [-bid["volume"] for bid in bids]

    assert all(prices[i] >= prices[i + 1] for i in range(len(prices) - 1))
    assert sum(volumes) == pytest.approx(unit.max_power, rel=1e-2)
    assert prices[0] == unit.max_price


def test_demand_energy_heuristic_elastic_strategy_raises_when_volume_exceeds_max(
    mock_market_config,
):
    """
    Tests that a ValueError is raised when first block bid volume exceeds max_power.
    """
    strategy = EnergyHeuristicElasticStrategy()
    unit = MockElasticDemand(
        elasticity=0.1,  # Extreme elasticity to trigger large volume
        max_price=500.0,
        num_bids=3,
        max_power=100,
        min_power=0,
        elasticity_model="isoelastic",
    )
    product_tuples = [(start, end, None)]

    with pytest.raises(ValueError, match="exceeds max power"):
        strategy.calculate_bids(unit, mock_market_config, product_tuples)


redispatch_start = datetime(2005, 6, 1, 0)
redispatch_end = datetime(2005, 6, 1, 3)
simple_redispatch_market_config = MarketConfig(
    market_id="simple_redispatch",
    market_products=[MarketProduct(timedelta(hours=1), 1, timedelta(hours=1))],
    additional_fields=["node", "min_power", "max_power"],
    opening_hours=rr.rrule(
        rr.HOURLY,
        dtstart=redispatch_start,
        until=redispatch_end,
        cache=True,
    ),
    opening_duration=timedelta(hours=1),
    volume_unit="MW",
    volume_tick=0.1,
    maximum_bid_volume=None,
    price_unit="€/MW",
    market_mechanism="redispatch",
)

index = pd.date_range(start=redispatch_start, end=redispatch_end, freq="1h")
forecaster = UnitForecaster(index=index)
unit_for_redispatch_with_availability = MockMinMaxUnit(forecaster=forecaster)
unit_for_redispatch_with_availability.max_power = 1000
unit_for_redispatch_with_availability.min_power = 100


@pytest.mark.parametrize(
    "dummy_availability, dummy_current_power, expected_bids_volume, expected_bids_max_power, expected_min_power",
    [
        (
            pd.Series(0, index=index),
            pd.Series([0, 0, 0, 0], index=index),
            pd.Series([0, 0, 0, 0], index=index),
            pd.Series(0, index=index),
            pd.Series(0, index=index),
        ),  # no availability, not running on EOM -> no volume to offer
        (
            pd.Series(0.0, index=index),
            pd.Series([0, 0, 0, 0], index=index),
            pd.Series([0, 0, 0, 0], index=index),
            pd.Series(0, index=index),
            pd.Series(0, index=index),
        ),  # same as above, but with float availability
        (
            pd.Series(0.5, index=index),
            pd.Series([0, 100, 200, 500], index=index),
            pd.Series([0, 100, 200, 500], index=index),
            pd.Series(500, index=index),
            pd.Series(100, index=index),
        ),  # 0.5 availability
        (
            pd.Series(1.0, index=index),
            pd.Series([0, 100, 200, 1000], index=index),
            pd.Series([0, 100, 200, 1000], index=index),
            pd.Series(1000, index=index),
            pd.Series(100, index=index),
        ),  # full availability
    ],
)
def test_naive_redispatch_strategy(
    dummy_availability,
    dummy_current_power,
    expected_bids_volume,
    expected_bids_max_power,
    expected_min_power,
):
    # test with simple redispatch market
    strategy = EnergyNaiveRedispatchStrategy()
    mc = simple_redispatch_market_config
    unit = unit_for_redispatch_with_availability

    mc.market_products = [
        MarketProduct(rd(hours=+1), 4, rd(hours=0))
    ]  # 4 products with 1h duration, starting at redispatch_start
    next_opening = redispatch_start
    products = get_available_products(mc.market_products, next_opening)
    assert len(products) == 4

    unit.forecaster.availability = dummy_availability
    unit.outputs["energy"] = dummy_current_power
    bids = strategy.calculate_bids(unit, mc, product_tuples=products)

    assert [bids[i]["volume"] for i in range(len(bids))] == pytest.approx(
        expected_bids_volume
    )
    assert [bids[i]["max_power"] for i in range(len(bids))] == pytest.approx(
        expected_bids_max_power
    )
    assert [bids[i]["min_power"] for i in range(len(bids))] == pytest.approx(
        expected_min_power
    )
    assert [bids[i]["p_nom"] for i in range(len(bids))] == pytest.approx(
        [unit.max_power] * len(bids)
    )
    assert [bids[i]["node"] for i in range(len(bids))] == pytest.approx(
        [unit.node] * len(bids)
    )
    assert [bids[i]["price"] for i in range(len(bids))] == pytest.approx(
        [3] * len(bids)
    )  # should bid at marginal cost, which are set to 3 in MockMinMaxUnit


def _correction_unit(mock_supports_minmax, values):
    unit = mock_supports_minmax
    unit.calculate_position_correction = lambda s, e, m: np.array(values, dtype=float)
    return unit


def test_correction_bids_prices_and_signs(mock_market_config, mock_supports_minmax):
    unit = _correction_unit(mock_supports_minmax, [-80, 30, 0])
    mc = mock_market_config
    products = [
        (start + timedelta(hours=h), start + timedelta(hours=h + 1), None)
        for h in range(3)
    ]
    bids = BaseStrategy().calculate_correction_bids(unit, mc, products)
    assert len(bids) == 2
    assert bids[0]["volume"] == -80
    assert bids[0]["price"] == mc.maximum_bid_price
    assert bids[0]["start_time"] == products[0][0]
    assert bids[0]["end_time"] == products[0][1]
    assert bids[0]["only_hours"] is None
    assert bids[0]["node"] == unit.node
    assert bids[1]["volume"] == 30
    assert bids[1]["price"] == mc.minimum_bid_price
    assert bids[1]["start_time"] == products[1][0]


def test_correction_bid_at_cap_survives_market_validation(mock_supports_minmax, caplog):
    mc = MarketConfig(
        market_id="Test",
        market_mechanism="pay_as_clear",
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=rd(hours=1),
        market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
        maximum_bid_price=3000.0,
        minimum_bid_price=-500.0,
    )
    unit = _correction_unit(mock_supports_minmax, [-80, 30])
    products = [
        (start + timedelta(hours=h), start + timedelta(hours=h + 1), None)
        for h in range(2)
    ]
    bids = BaseStrategy().calculate_correction_bids(unit, mc, products)
    role = MarketRole(marketconfig=mc)
    role.open_auctions = set(products)
    with caplog.at_level("WARNING"):
        role.validate_orderbook(bids, ("addr", "agent"))
    assert bids[0]["price"] == 3000.0
    assert bids[1]["price"] == -500.0
    assert not [r for r in caplog.records if "price" in r.getMessage()]


_ID_MC = MarketConfig(
    market_id="ID",
    opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
    opening_duration=rd(hours=1),
    market_mechanism="pay_as_clear",
    market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
    maximum_bid_price=3000.0,
    minimum_bid_price=-500.0,
)


def _hour_products(n=1):
    return [
        (start + timedelta(hours=h), start + timedelta(hours=h + 1), None)
        for h in range(n)
    ]


def _renewable(position, id_availability):
    index = pd.date_range(start, periods=4, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        availability_forecast={"ID": [id_availability / 1000] * 4},
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    pp = PowerPlant(
        id="ren",
        unit_operator="uo",
        technology="wind",
        bidding_strategies={"ID": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=0,
        efficiency=1,
        fuel_type="lignite",
        emission_factor=0,
        forecaster=forecaster,
    )
    pp.outputs["energy"].loc[index[0] : index[-1]] = position
    return pp


def test_renewable_forced_buyback():
    pp = _renewable(position=300, id_availability=220)
    bids = EnergyNaiveStrategy().calculate_bids(pp, _ID_MC, _hour_products())
    mc = pp.calculate_marginal_cost(start, 0)
    forced = [b for b in bids if b["price"] == _ID_MC.maximum_bid_price]
    assert [b["volume"] for b in forced] == pytest.approx([-80])
    curtail = [b for b in bids if b["price"] == mc]
    assert [b["volume"] for b in curtail] == pytest.approx([-220])
    assert sum(b["volume"] for b in bids if b["volume"] < 0) == pytest.approx(-300)
    assert not [b for b in bids if b["volume"] > 0]


def test_renewable_voluntary_curtailment():
    pp = _renewable(position=200, id_availability=400)
    bids = EnergyNaiveStrategy().calculate_bids(pp, _ID_MC, _hour_products())
    mc = pp.calculate_marginal_cost(start, 0)
    buys = [b for b in bids if b["volume"] < 0]
    assert len(buys) == 1
    assert buys[0]["volume"] == pytest.approx(-200)
    assert buys[0]["price"] == pytest.approx(mc)
    assert not [b for b in bids if b["price"] == _ID_MC.maximum_bid_price]


def test_renewable_sells_extra():
    pp = _renewable(position=200, id_availability=500)
    bids = EnergyNaiveStrategy().calculate_bids(pp, _ID_MC, _hour_products())
    mc = pp.calculate_marginal_cost(start, 0)
    sells = [b for b in bids if b["volume"] > 0]
    assert len(sells) == 1
    assert sells[0]["volume"] == pytest.approx(300)
    assert sells[0]["price"] == pytest.approx(mc)


def _demand(position, forecast):
    index = pd.date_range(start, periods=4, freq="h")
    forecaster = DemandForecaster(
        index,
        demand=-1000,
        demand_forecast={"ID": [forecast] * 4},
    )
    dem = Demand(
        id="dem",
        unit_operator="uo",
        technology="energy",
        bidding_strategies={"ID": DemandEnergyNaiveStrategy()},
        max_power=-1000,
        min_power=0,
        forecaster=forecaster,
        price=3000,
    )
    if position is not None:
        dem.outputs["energy"].loc[index[0] : index[-1]] = position
    return dem


def test_demand_single_market_unchanged():
    dem = _demand(position=None, forecast=-1000)
    # realised demand -1000 and no position: exactly one bid for the remaining demand
    mc = MarketConfig(
        market_id="EOM",
        opening_hours=rr.rrule(rr.HOURLY),
        opening_duration=timedelta(hours=1),
        market_mechanism="not needed",
        market_products=[MarketProduct(timedelta(hours=1), 1, timedelta(hours=1))],
    )
    products = _hour_products()
    old = EnergyNaiveStrategy().calculate_bids(dem, mc, products)
    new = DemandEnergyNaiveStrategy().calculate_bids(dem, mc, products)
    assert len(new) == 1
    assert new[0]["volume"] == -1000
    assert new[0]["price"] == 3000
    assert new == old or [b["volume"] for b in old] == [-1000]


def test_demand_overbought_sells_at_floor():
    dem = _demand(position=-980, forecast=-950)
    bids = DemandEnergyNaiveStrategy().calculate_bids(dem, _ID_MC, _hour_products())
    assert len(bids) == 1
    assert bids[0]["volume"] == pytest.approx(30)
    assert bids[0]["price"] == _ID_MC.minimum_bid_price


def test_demand_underbought_buys_at_wtp():
    dem = _demand(position=-950, forecast=-980)
    bids = DemandEnergyNaiveStrategy().calculate_bids(dem, _ID_MC, _hour_products())
    assert len(bids) == 1
    assert bids[0]["volume"] == pytest.approx(-30)
    assert bids[0]["price"] == 3000


if __name__ == "__main__":
    # run pytest and enable prints
    import pytest

    pytest.main(["-s", __file__])

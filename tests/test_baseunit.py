# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import timedelta

import pandas as pd
import pytest

from assume.common.base import BaseStrategy, BaseUnit
from assume.common.forecaster import PowerplantForecaster
from assume.common.market_objects import MarketConfig, Orderbook, Product


class BasicStrategy(BaseStrategy):
    def calculate_bids(
        self,
        unit: BaseUnit,
        market_config: MarketConfig,
        product_tuples: list[Product],
        **kwargs,
    ) -> Orderbook:
        bids = []
        for product in product_tuples:
            bids.append(
                {
                    "start_time": product[0],
                    "end_time": product[1],
                    "only_hours": product[2],
                    "price": 10,
                    "volume": 20,
                }
            )
        return bids


@pytest.fixture(params=["1h", "15min"])
def base_unit(request) -> BaseUnit:
    # Create a PowerPlant instance with some example parameters
    index = pd.date_range("2022-01-01", periods=4, freq=request.param)
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        fuel_prices={"others": [10, 11, 12, 13], "co2": [10, 20, 30, 30]},
    )
    return BaseUnit(
        id="test_pp",
        unit_operator="test_operator",
        technology="base",
        bidding_strategies={"EOM": BasicStrategy()},
        forecaster=forecaster,
        index=forecaster.index,
    )


def test_init_function(base_unit, mock_market_config):
    assert base_unit.id == "test_pp"
    assert base_unit.unit_operator == "test_operator"
    assert base_unit.technology == "base"
    assert base_unit.as_dict() == {
        "id": "test_pp",
        "technology": "base",
        "unit_operator": "test_operator",
        "node": "node0",
        "unit_type": "base_unit",
    }


def test_output_before(base_unit, mock_market_config):
    index = base_unit.index
    assert base_unit.get_output_before(index[0], "energy") == 0
    assert base_unit.get_output_before(index[0], "fictional_product_type") == 0

    base_unit.outputs["energy"][index[0]] = 100
    assert base_unit.get_output_before(index[1], "energy") == 100
    assert base_unit.get_output_before(index[0], "energy") == 0

    base_unit.outputs["fictional_product_type"][index[0]] = 200
    assert base_unit.get_output_before(index[1], "fictional_product_type") == 200
    assert base_unit.get_output_before(index[0], "fictional_product_type") == 0


def test_calculate_bids(base_unit, mock_market_config):
    index = base_unit.index
    start = index[0]
    end = index[1]
    product_tuples = [(start, end, None)]
    bids = base_unit.calculate_bids(mock_market_config, product_tuples)
    assert bids == [
        {
            "start_time": start,
            "end_time": end,
            "only_hours": None,
            "price": 10,
            "volume": 20,
        }
    ]

    orderbook = [
        {
            "start_time": start,
            "end_time": end,
            "only_hours": None,
            "price": 10,
            "volume": 10,
            "accepted_price": 11,
            "accepted_volume": 10,
        }
    ]
    # mock calculate_marginal_cost
    base_unit.calculate_marginal_cost = lambda *x: 10
    base_unit.set_dispatch_plan(mock_market_config, orderbook)
    base_unit.calculate_generation_cost(index[0], index[1], "energy")
    base_unit.calculate_cashflow_and_reward(mock_market_config, orderbook)

    # we apply the dispatch plan of 10 MW
    assert base_unit.outputs["energy"][start] == 10
    assert base_unit.outputs["energy_generation_costs"][start] == 10 * 10
    # we received more, as accepted_price is higher
    assert base_unit.outputs["energy_cashflow"][start] == 10 * 11

    # we somehow sold an additional 10 MW
    base_unit.set_dispatch_plan(mock_market_config, orderbook)
    base_unit.calculate_generation_cost(index[0], index[1], "energy")
    base_unit.calculate_cashflow_and_reward(mock_market_config, orderbook)

    # the final output should be 10+10
    assert base_unit.outputs["energy"][start] == 20
    # the marginal cost for this volume should be twice as much too
    assert base_unit.outputs["energy_generation_costs"][start] == 200
    assert base_unit.outputs["energy_cashflow"][start] == 20 * 11


def test_calculate_multi_bids(base_unit, mock_market_config):
    index = base_unit.index

    # when we set
    orderbook = [
        {
            "start_time": index[0],
            "end_time": index[1],
            "only_hours": None,
            "price": 10,
            "volume": 10,
            "accepted_price": 11,
            "accepted_volume": 10,
        },
        {
            "start_time": index[1],
            "end_time": index[2],
            "only_hours": None,
            "price": 10,
            "volume": 10,
            "accepted_price": 11,
            "accepted_volume": 10,
        },
    ]
    # mock calculate_marginal_cost
    base_unit.calculate_marginal_cost = lambda *x: 10
    base_unit.set_dispatch_plan(mock_market_config, orderbook)
    base_unit.calculate_generation_cost(index[0], index[1], "energy")
    base_unit.calculate_cashflow_and_reward(mock_market_config, orderbook)

    assert base_unit.outputs["energy"][index[0]] == 10
    assert base_unit.outputs["energy_generation_costs"][index[0]] == 100
    assert base_unit.outputs["energy_cashflow"][index[0]] == 110
    assert base_unit.outputs["energy"][index[1]] == 10
    assert base_unit.outputs["energy_generation_costs"][index[1]] == 100
    assert base_unit.outputs["energy_cashflow"][index[1]] == 110

    base_unit.set_dispatch_plan(mock_market_config, orderbook)
    base_unit.calculate_generation_cost(index[0], index[1], "energy")
    base_unit.calculate_cashflow_and_reward(mock_market_config, orderbook)

    # should be correctly applied for the sum, even if different hours are applied
    assert base_unit.outputs["energy"][index[0]] == 20
    assert base_unit.outputs["energy_generation_costs"][index[0]] == 200
    assert base_unit.outputs["energy_cashflow"][index[0]] == 220
    assert base_unit.outputs["energy"][index[1]] == 20
    assert base_unit.outputs["energy_generation_costs"][index[1]] == 200
    assert base_unit.outputs["energy_cashflow"][index[1]] == 220

    # in base_unit - this should not do anything but get return the energy dispatch
    assert (
        base_unit.execute_current_dispatch(index[0], index[2])
        == base_unit.outputs["energy"][index[0] : index[2]]
    ).all()


def test_calculate_generation_cost_unaligned_start(base_unit):
    # the units operator passes start = last dispatch + 1 s, which is not on the index grid
    index = base_unit.index
    calculated = []

    def marginal_cost(t, power):
        calculated.append(t)
        return 10

    base_unit.calculate_marginal_cost = marginal_cost
    base_unit.outputs["energy"].loc[index[0] : index[3]] = 1

    base_unit.calculate_generation_cost(
        index[1] + timedelta(seconds=1), index[3], "energy"
    )

    # only the steps after the last dispatch are calculated
    assert calculated == [index[2], index[3]]
    assert base_unit.outputs["energy_generation_costs"][index[1]] == 0
    assert base_unit.outputs["energy_generation_costs"][index[2]] == 10

    # a start before the index is clamped to the first step
    calculated.clear()
    base_unit.calculate_generation_cost(
        index[0] - timedelta(hours=1), index[1], "energy"
    )
    assert calculated == [index[0], index[1]]


def test_clear_empty_bids(base_unit, mock_market_config):
    # Test empty bids
    bids = []
    index = base_unit.index
    for start in index:
        bids.append(
            {
                "start_time": start,
                "end_time": start + timedelta(hours=1),
                "only_hours": None,
                "price": 10,
                "volume": 0,
            }
        )
    assert (
        base_unit.bidding_strategies[mock_market_config.market_id].remove_empty_bids(
            bids
        )
        == []
    )

    # Test non-empty bids
    non_empty_bids = []
    for start in index:
        non_empty_bids.append(
            {
                "start_time": start,
                "end_time": start + timedelta(hours=1),
                "only_hours": None,
                "price": 10,
                "volume": 10,
            }
        )
    non_empty_bids_result = base_unit.bidding_strategies[
        mock_market_config.market_id
    ].remove_empty_bids(non_empty_bids)
    assert non_empty_bids_result == non_empty_bids

    # Test mixed empty and non-empty bids
    mixed_bids = []
    for start in index:
        mixed_bids.append(
            {
                "start_time": start,
                "end_time": start + timedelta(hours=1),
                "only_hours": None,
                "price": 10,
                "volume": 0 if start.hour % 2 == 0 else 10,
            }
        )
    mixed_bids_result = base_unit.bidding_strategies[
        mock_market_config.market_id
    ].remove_empty_bids(mixed_bids)
    assert mixed_bids_result == [bid for bid in mixed_bids if bid["volume"] > 0]


if __name__ == "__main__":
    pytest.main(["-s", __file__])


def _market_order(unit, volume, price, market_id=None):
    order = {
        "start_time": unit.index[1],
        "end_time": unit.index[1] + unit.index.freq,
        "accepted_volume": volume,
        "accepted_price": price,
    }
    if market_id is not None:
        order["market_id"] = market_id
    return order


def test_per_market_volume_tracking(base_unit):
    da = MarketConfig(market_id="DA", product_type="energy")
    id_ = MarketConfig(market_id="ID", product_type="energy")
    base_unit.set_dispatch_plan(da, [_market_order(base_unit, 100, 50, "DA")])
    base_unit.set_dispatch_plan(id_, [_market_order(base_unit, -30, 60, "ID")])
    t = base_unit.index[1]
    assert base_unit.outputs["energy"][t] == 70
    assert base_unit.outputs["energy_DA"][t] == 100
    assert base_unit.outputs["energy_ID"][t] == -30


def test_per_market_cashflow_sums_to_total(base_unit):
    for mid, vol, price in [("DA", 100, 50), ("ID", -30, 60)]:
        base_unit.calculate_cashflow(
            "energy", [_market_order(base_unit, vol, price, mid)]
        )
    out = base_unit.outputs
    for t in base_unit.index:
        assert out["energy_cashflow"][t] == pytest.approx(
            out["energy_cashflow_DA"][t] + out["energy_cashflow_ID"][t]
        )
    assert out["energy_cashflow_DA"][base_unit.index[1]] != 0


def test_order_without_market_id_unchanged(base_unit):
    mc = MarketConfig(market_id="EOM", product_type="energy")
    order = _market_order(base_unit, 40, 10)
    base_unit.set_dispatch_plan(mc, [order])
    base_unit.calculate_cashflow("energy", [order])
    assert base_unit.outputs["energy"][base_unit.index[1]] == 40
    assert not any(k.endswith(("_EOM", "_DA", "_ID")) for k in base_unit.outputs)


class _CostUnit(BaseUnit):
    def calculate_marginal_cost(self, start, power):
        return 40


@pytest.fixture
def cost_unit() -> _CostUnit:
    index = pd.date_range("2022-01-01", periods=4, freq="1h")
    forecaster = PowerplantForecaster(index, availability=1)
    return _CostUnit(
        id="cost_pp",
        unit_operator="test_operator",
        technology="base",
        bidding_strategies={"DA": BasicStrategy(), "ID": BasicStrategy()},
        forecaster=forecaster,
        index=forecaster.index,
    )


def test_operating_cost_default_cost_is_abs_volume_times_mc(cost_unit):
    cost_unit.outputs["energy"].loc[cost_unit.index[1] : cost_unit.index[2]] = [
        100,
        -50,
    ]
    cost = cost_unit.calculate_operating_cost(cost_unit.index[0], cost_unit.index[-1])
    assert list(cost) == [0, 4000, 2000, 0]


def test_profit_attribution_sums_to_total(cost_unit):
    da = MarketConfig(market_id="DA", product_type="energy")
    id_ = MarketConfig(market_id="ID", product_type="energy")
    for mc, vol, price in [(da, 100, 60), (id_, -100, 30)]:
        order = _market_order(cost_unit, vol, price, mc.market_id)
        cost_unit.set_dispatch_plan(mc, [order])
        cost_unit.calculate_cashflow("energy", [order])
    # profit calculated at the end of cashflow: DA sells at 60 with mc 40, then
    # ID buys back at 30, avoiding the 40 cost
    out = cost_unit.outputs
    t = cost_unit.index[1]
    assert out["energy_total_costs_DA"][t] == pytest.approx(4000)
    assert out["energy_total_costs_ID"][t] == pytest.approx(-4000)
    assert out["energy_profit_DA"][t] == pytest.approx(6000 - 4000)
    assert out["energy_profit_ID"][t] == pytest.approx(-3000 + 4000)
    assert out["energy_profit_ID"][t] > 0
    final_cost = cost_unit.calculate_operating_cost(
        cost_unit.index[0], cost_unit.index[-1]
    )
    total_profit = out["energy_profit_DA"].data + out["energy_profit_ID"].data
    assert total_profit.sum() == pytest.approx(
        out["energy_cashflow"].data.sum() - final_cost.sum()
    )


def test_capacity_product_not_attributed(cost_unit):
    mc = MarketConfig(market_id="CAP", product_type="capacity_pos")
    order = _market_order(cost_unit, 10, 5, "CAP")
    cost_unit.set_dispatch_plan(mc, [order])
    cost_unit.calculate_cashflow("capacity_pos", [order])
    assert "capacity_pos_total_costs_CAP" not in cost_unit.outputs
    assert "capacity_pos_profit_CAP" not in cost_unit.outputs


def test_minmax_unit_without_start_cost_attributes_has_no_start_cost():
    """SupportsMinMax units that define no start-up parameters (DSM units, custom
    units) must not crash in the per-market cost attribution: missing attributes
    mean no start-up cost."""
    from tests.conftest import MockMinMaxUnit

    index = pd.date_range("2023-01-01", periods=4, freq="h")
    unit = MockMinMaxUnit(forecaster=PowerplantForecaster(index, availability=1))
    mc = MarketConfig(market_id="EOM", product_type="energy")
    order = {
        "start_time": unit.index[1],
        "end_time": unit.index[1] + unit.index.freq,
        "accepted_volume": 5.0,
        "accepted_price": 10,
        "market_id": "EOM",
        "only_hours": None,
        "price": 1,
        "volume": 5,
    }
    unit.set_dispatch_plan(mc, [order])

    assert unit.get_starting_costs(-1) == 0
    assert unit.get_starting_costs(-100) == 0
    # running cost only: 5 MW at a marginal cost of 3
    assert unit.outputs["energy_total_costs_EOM"][unit.index[1]] == pytest.approx(15)
    assert unit.outputs["energy_total_costs_EOM"].data.sum() == pytest.approx(15)

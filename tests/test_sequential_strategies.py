# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

import pandas as pd
import pytest

from assume.common.forecaster import (
    DemandForecaster,
    PowerplantForecaster,
    UnitForecaster,
)
from assume.common.market_objects import MarketConfig
from assume.strategies import bidding_strategies
from assume.strategies.sequential_strategies import (
    DemandSequentialFixedStrategy,
    PowerplantSequentialHeuristicStrategy,
    RenewableSequentialHeuristicStrategy,
    StorageSequentialHeuristicStrategy,
)
from assume.units.sequential_units import (
    SequentialDemand,
    SequentialPowerPlant,
    SequentialStorage,
)

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
def gas():
    forecaster = PowerplantForecaster(
        INDEX,
        availability=1,
        fuel_prices={"natural gas": 25, "co2": 80},
        market_prices={},
    )
    strategy = PowerplantSequentialHeuristicStrategy(bal_markup=10)
    return SequentialPowerPlant(
        id="gas",
        unit_operator="op",
        technology="ccgt",
        bidding_strategies={"DA": strategy, "ID": strategy, "BAL": strategy},
        index=forecaster.index,
        max_power=1000,
        min_power=0,
        efficiency=0.55,
        fuel_type="natural gas",
        emission_factor=0.2,
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


def test_gas_da_offers_full_capacity(gas):
    prods = products(24, datetime(2022, 1, 1))
    bids = gas.calculate_bids(DA, prods)
    assert len(bids) == 24
    for bid, prod in zip(bids, prods):
        mc = gas.calculate_marginal_cost(prod[0], 1000)
        assert bid["volume"] == pytest.approx(1000)
        assert bid["price"] == pytest.approx(mc)
    assert bids[0]["price"] == pytest.approx(74.5, abs=0.5)


def test_gas_id_incremental(gas):
    gas.outputs["energy"].at[T] = 600
    bids = gas.calculate_bids(ID, products())
    mc = gas.calculate_marginal_cost(T, 600)
    assert sorted(b["volume"] for b in bids) == pytest.approx([-600, 400])
    assert all(b["price"] == pytest.approx(mc) for b in bids)


def test_gas_id_buyback_never_below_min_power(gas):
    gas.min_power = 200
    gas.outputs["energy"].at[T] = 600
    bids = gas.calculate_bids(ID, products())
    assert sorted(b["volume"] for b in bids) == pytest.approx([-400, 400])


def test_gas_balancing_inc_dec(gas):
    gas.outputs["energy"].at[T] = 700
    bids = gas.calculate_bids(BAL, products())
    mc = gas.calculate_marginal_cost(T, 700)
    by_volume = {round(b["volume"]): b for b in bids}
    assert set(by_volume) == {300, -700}
    assert by_volume[300]["price"] == pytest.approx(mc + 10)
    assert by_volume[-700]["price"] == pytest.approx(mc - 10)
    assert all(b["fpn"] == 700 and b["position"] == 700 for b in bids)


def test_gas_balancing_no_negative_capacity(gas):
    gas.outputs["energy"].at[T] = 1000
    bids = gas.calculate_bids(BAL, products())
    assert [b["volume"] for b in bids] == pytest.approx([-1000])


def test_gas_balancing_notification_when_off(gas):
    gas.forecaster.availability[:] = 0
    bids = gas.calculate_bids(BAL, products())
    assert len(bids) == 1 and bids[0]["volume"] == 0 and bids[0]["fpn"] == 0


def test_gas_fpn_follows_position(gas):
    gas.outputs["energy"].at[T] = 400
    bids = gas.calculate_bids(BAL, products())
    assert all(b["fpn"] == 400 for b in bids)


def test_gas_registered():
    assert (
        bidding_strategies["powerplant_sequential_heuristic"]
        is PowerplantSequentialHeuristicStrategy
    )


# --- storage -----------------------------------------------------------------

DAY = datetime(2022, 1, 1)


def make_storage(initial_soc=0.5, da_prices=50.0, bal_markup=0.0):
    """100 MW / 200 MWh battery, eff 0.9/0.9; ``da_prices=None`` -> no DA forecast."""
    market_prices = {"EOM": 50} if da_prices is None else {"DA": da_prices}
    forecaster = UnitForecaster(INDEX, market_prices=market_prices)
    strategy = StorageSequentialHeuristicStrategy(bal_markup=bal_markup)
    return SequentialStorage(
        id="bat",
        unit_operator="op",
        technology="battery",
        bidding_strategies={"DA": strategy, "ID": strategy, "BAL": strategy},
        forecaster=forecaster,
        max_power_charge=-100,
        max_power_discharge=100,
        capacity=200,
        efficiency_charge=0.9,
        efficiency_discharge=0.9,
        min_soc=0,
        max_soc=1,
        initial_soc=initial_soc,
    )


def record_realised(unit, market_config, prices):
    strategy = unit.bidding_strategies[market_config.market_id]
    orders = [
        {
            "start_time": DAY + h * H,
            "end_time": DAY + (h + 1) * H,
            "accepted_volume": 0,
            "accepted_price": price,
        }
        for h, price in prices.items()
    ]
    strategy.calculate_reward(unit, market_config, orders)


def roll_soc(unit, soc, volume):
    if volume > 0:
        return soc - volume / unit.efficiency_discharge / unit.capacity
    return soc - volume * unit.efficiency_charge / unit.capacity


def step_prices():
    return [20.0] * 6 + [100.0] * 6 + [60.0] * 12


def test_storage_no_inc_at_min_soc():
    bat = make_storage(initial_soc=0)
    bat.outputs["energy"].at[T] = 50  # sold a discharge it cannot deliver
    bids = bat.calculate_bids(BAL, products())
    assert not [b for b in bids if b["volume"] > 0]
    assert all(b["fpn"] <= 0 for b in bids)
    assert all(b["position"] == 50 for b in bids)
    # the whole charge range is offered as dec
    assert [b["volume"] for b in bids] == pytest.approx([-100])


def test_storage_no_dec_at_max_soc():
    bat = make_storage(initial_soc=1)
    bat.outputs["energy"].at[T] = -50  # bought a charge it cannot absorb
    bids = bat.calculate_bids(BAL, products())
    assert not [b for b in bids if b["volume"] < 0]
    assert all(b["fpn"] >= 0 for b in bids)
    assert [b["volume"] for b in bids] == pytest.approx([100])


def test_storage_arbitrage_da():
    bat = make_storage(initial_soc=0, da_prices=step_prices())
    prods = products(12, DAY)
    bids = bat.calculate_bids(DA, prods)
    by_hour = {(b["start_time"] - DAY) // H: b for b in bids}
    assert by_hour, "expected arbitrage bids"
    assert all(b["volume"] < 0 for h, b in by_hour.items() if h < 6)
    assert all(b["volume"] > 0 for h, b in by_hour.items() if h >= 6)
    assert any(h < 6 for h in by_hour) and any(h >= 6 for h in by_hour)
    # v = 60 in every hour: charge at v*0.9, discharge at v/0.9
    assert all(
        b["price"] == pytest.approx(54 if b["volume"] < 0 else 60 / 0.9) for b in bids
    )
    soc = 0.0
    for h in range(12):
        soc = roll_soc(bat, soc, by_hour[h]["volume"] if h in by_hour else 0)
        assert -1e-9 <= soc <= 1 + 1e-9
    # fills the battery (100, 100, 22.2) and empties it (100, 80)
    assert [by_hour[h]["volume"] for h in sorted(by_hour)] == pytest.approx(
        [-100, -100, -200 / 9, 100, 80]
    )


def test_storage_id_uses_realised_da_price():
    bat = make_storage(initial_soc=0, da_prices=50.0)
    prods = products(12, DAY)
    assert bat.calculate_bids(ID, prods) == []  # flat forecast: no spread
    record_realised(bat, DA, {h: 20.0 if h < 6 else 100.0 for h in range(12)})
    bids = bat.calculate_bids(ID, prods)
    hours = {(b["start_time"] - DAY) // H: b["volume"] for b in bids}
    assert hours
    assert all(v < 0 for h, v in hours.items() if h < 6)
    assert all(v > 0 for h, v in hours.items() if h >= 6)
    assert any(h < 6 for h in hours) and any(h >= 6 for h in hours)


def test_storage_missing_prices_no_keyerror():
    bat = make_storage(da_prices=None)
    prods = products(24, DAY)
    assert bat.calculate_bids(DA, prods) == []
    assert bat.calculate_bids(ID, prods) == []
    bids = bat.calculate_bids(BAL, products())
    assert len(bids) == 1
    assert bids[0]["volume"] == 0 and bids[0]["fpn"] == 0


def test_storage_id_offers_only_remaining_soc_headroom():
    # soc 0.25 -> SOC allows 0.25 * 200 * 0.9 = 45 MW of discharge in hour 0
    bat = make_storage(initial_soc=0.25, da_prices=50.0)
    bat.outputs["energy"].at[DAY] = 40  # DA discharge position
    record_realised(bat, DA, {0: 100.0})
    (bid,) = bat.calculate_bids(ID, products(1, DAY))
    assert bid["volume"] == pytest.approx(5)
    v = (100 + 12 * 50) / 13
    assert bid["price"] == pytest.approx(v / 0.9)


def test_storage_balancing_fpn_plus_bal_soc_feasible():
    bat = make_storage(initial_soc=0.25, da_prices=50.0, bal_markup=5)
    bat.outputs["energy"].at[DAY] = 40
    bids = bat.calculate_bids(BAL, products(1, DAY))
    by_sign = {b["volume"] > 0: b for b in bids}
    assert all(b["fpn"] == pytest.approx(40) for b in bids)
    assert by_sign[True]["volume"] == pytest.approx(5)  # up to 45 MW total
    assert by_sign[False]["volume"] == pytest.approx(-140)  # down to -100 MW
    assert by_sign[True]["price"] == pytest.approx(50 / 0.9 + 5)
    assert by_sign[False]["price"] == pytest.approx(50 * 0.9 - 5)
    for b in bids:
        physical = b["fpn"] + b["volume"]
        soc = roll_soc(bat, 0.25, physical)
        assert -1e-9 <= soc <= 1 + 1e-9
        assert -100 - 1e-9 <= physical <= 100 + 1e-9


def test_storage_fpn_clipped_to_soc_limit():
    bat = make_storage(initial_soc=0.25, da_prices=50.0)
    bat.outputs["energy"].at[DAY] = 100
    bids = bat.calculate_bids(BAL, products(1, DAY))
    assert all(b["fpn"] == pytest.approx(45) for b in bids)
    assert all(b["position"] == 100 for b in bids)
    assert not [b for b in bids if b["volume"] > 0]


def test_storage_planned_soc_rolls_position_from_last_settlement():
    bat = make_storage(initial_soc=0, da_prices=50.0)
    strategy = bat.bidding_strategies["DA"]
    bat.outputs["energy"].loc[DAY : DAY + 2 * H] = -100
    assert strategy.planned_soc(bat, DAY) == pytest.approx(0)
    assert strategy.planned_soc(bat, DAY + 2 * H) == pytest.approx(0.9)
    # hour 0 settles with 50 MW of inc accepted: only 50 MW charged
    bat.settle(DAY, fpn=-100, accepted_balancing=50, price=0)
    strategy.on_settlement(bat, DAY)
    assert strategy.planned_soc(bat, DAY + H) == pytest.approx(0.225)
    assert strategy.planned_soc(bat, DAY + 2 * H) == pytest.approx(0.675)


def test_storage_planned_soc_rolls_with_feasible_dispatch():
    bat = make_storage(initial_soc=0.25, da_prices=50.0)
    strategy = bat.bidding_strategies["DA"]
    bat.outputs["energy"].at[DAY] = 100  # only 45 MW deliverable
    assert strategy.planned_soc(bat, DAY + H) == pytest.approx(0)


def test_storage_settlement_of_fpn_plus_bal_never_clips(caplog):
    bat = make_storage(initial_soc=0.25, da_prices=50.0)
    strategy = bat.bidding_strategies["BAL"]
    bat.outputs["energy"].at[DAY] = 40
    bids = bat.calculate_bids(BAL, products(1, DAY))
    inc = max(b["volume"] for b in bids)
    with caplog.at_level("WARNING"):
        bat.settle(DAY, fpn=bids[0]["fpn"], accepted_balancing=inc, price=0)
        strategy.on_settlement(bat, DAY)
    assert "clipped" not in caplog.text
    assert bat.outputs["soc"].at[DAY + H] == pytest.approx(0)


def test_storage_registered():
    assert (
        bidding_strategies["storage_sequential_heuristic"]
        is StorageSequentialHeuristicStrategy
    )

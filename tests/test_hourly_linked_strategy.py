# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import timedelta

import pandas as pd
import pytest
from dateutil import rrule as rr

from assume.common.forecaster import DemandForecaster, PowerplantForecaster
from assume.common.market_objects import MarketConfig, MarketProduct
from assume.markets.clearing_algorithms import ComplexClearingRole
from assume.strategies import (
    DemandEnergyNaiveStrategy,
    EnergyNaiveStrategy,
    bidding_strategies,
)
from assume.strategies.advanced_orders import (
    EnergyHeuristicFlexableHourlyLinkedStrategy,
    determine_on_case,
)
from assume.strategies.flexable import (
    calculate_EOM_price_if_off,
    calculate_EOM_price_if_on,
)
from assume.units import Demand, PowerPlant

# CCGT reference (spec section 5.6/6.4): fuel 25, co2 25, emission factor 0.2,
# efficiency 0.55, max 100 MW, min 40 MW
CCGT_LOSS_40 = (
    0.178749 * 0.4**4
    - 0.653192 * 0.4**3
    + 0.964704 * 0.4**2
    - 0.805845 * 0.4
    + 0.315584
)
COST_M = 40 * 30 / (0.55 - CCGT_LOSS_40)  # ~2729.6
COST_U = 100 * 30 / 0.55  # ~5454.5


# =============================================================================
# determine_on_case
# =============================================================================


def test_switch_prices_ccgt_r0_is_on_b():
    case, prices = determine_on_case(COST_M, COST_U, 40, 100, 0, True)
    assert case == "on-b"
    assert prices["p_mU"] == pytest.approx(45.4, abs=0.05)
    assert prices["p_0U"] == pytest.approx(54.5, abs=0.05)
    assert prices["p_0m"] == pytest.approx(68.2, abs=0.05)
    assert prices["p_mU"] == pytest.approx((COST_U - COST_M) / 60)
    assert prices["p_0U"] == pytest.approx(COST_U / 100)
    assert prices["p_0m"] == pytest.approx(COST_M / 40)


def test_switch_prices_ccgt_hot_start_is_on_c():
    case, prices = determine_on_case(COST_M, COST_U, 40, 100, 2265, True)
    assert case == "on-c"
    assert prices["p_mU"] == pytest.approx(45.4, abs=0.05)
    assert prices["p_0U"] == pytest.approx((COST_U - 2265) / 100)
    assert prices["p_0m"] == pytest.approx((COST_M - 2265) / 40)
    assert prices["p_0m"] < prices["p_mU"]


def test_on_a_when_shutdown_not_allowed():
    for R in (0, 2265):
        case, prices = determine_on_case(COST_M, COST_U, 40, 100, R, False)
        assert case == "on-a"
        assert prices["p_mU"] == pytest.approx((COST_U - COST_M) / 60)


def test_switch_prices_degenerate_ranges():
    # U == m: no flexible range, p_mU falls back to p_0U (and on-b)
    case, prices = determine_on_case(COST_U, COST_U, 100, 100, 0, True)
    assert prices["p_mU"] == pytest.approx(prices["p_0U"])
    assert case == "on-b"
    # m == 0: running at zero always beats a restart, never a shutdown block
    case, prices = determine_on_case(0.0, COST_U, 0, 100, 10, True)
    assert prices["p_0m"] == float("-inf")
    assert case == "on-c"


# =============================================================================
# calculate_bids
# =============================================================================

INDEX = pd.date_range("2023-07-01", periods=24, freq="h")
HOUR = timedelta(hours=1)
R_HOT = 2265.0  # start-up cost making a running CCGT fall into on-c


def _market_config(market_id="ID"):
    return MarketConfig(
        market_id=market_id,
        product_type="energy",
        market_products=[MarketProduct(HOUR, 2, HOUR)],
        additional_fields=["bid_type", "min_acceptance_ratio", "parent_bid_id"],
        opening_hours=rr.rrule(
            rr.HOURLY, dtstart=INDEX[0], until=INDEX[-1], cache=True
        ),
        opening_duration=HOUR,
        maximum_bid_volume=100000,
        maximum_bid_price=3000,
        minimum_bid_price=-500,
        market_mechanism="complex_clearing",
    )


def _ccgt(
    positions=None,
    start_cost=0.0,
    min_operating_time=1,
    min_down_time=1,
    ramp_up=None,
):
    """100 MW CCGT (min 40 MW) with partial-load efficiency and an hourly position."""
    strategy = EnergyHeuristicFlexableHourlyLinkedStrategy()
    forecaster = PowerplantForecaster(
        INDEX,
        availability=1,
        fuel_prices={"natural gas": 25, "co2": 25},
        market_prices={"DA": 50, "ID": 50},
    )
    pp = PowerPlant(
        id="ccgt",
        unit_operator="op",
        technology="combined cycle gas turbine",
        bidding_strategies={"DA": strategy, "ID": strategy},
        index=forecaster.index,
        max_power=100,
        min_power=40,
        efficiency=0.55,
        fuel_type="natural gas",
        emission_factor=0.2,
        forecaster=forecaster,
        partial_load_eff=True,
        hot_start_cost=start_cost / 100,
        warm_start_cost=start_cost / 100,
        cold_start_cost=start_cost / 100,
        min_operating_time=min_operating_time,
        min_down_time=min_down_time,
        ramp_up=ramp_up,
    )
    for hour, volume in (positions or {}).items():
        pp.outputs["energy"].at[INDEX[hour]] = volume
    return pp, strategy


def _products(*hours):
    return [(INDEX[h], INDEX[h] + HOUR, None) for h in hours]


def _bid(bids, **match):
    found = [b for b in bids if all(b.get(k) == v for k, v in match.items())]
    assert len(found) == 1, f"{match} matched {len(found)} bids in {bids}"
    return found[0]


def _total(volume):
    return sum(volume.values()) if isinstance(volume, dict) else volume


def _running(hours, volume=70):
    return {h: volume for h in hours}


def test_registered():
    assert (
        bidding_strategies["powerplant_energy_heuristic_flexable_hourly_linked"]
        is EnergyHeuristicFlexableHourlyLinkedStrategy
    )


def test_off_case_start_and_flex_bids():
    pp, strategy = _ccgt(start_cost=R_HOT)
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 2

    s_bid = _bid(bids, bid_type="BB")
    assert s_bid["bid_id"] == "ccgt_S_20230701T10"
    assert s_bid["volume"] == {t: 40}
    assert s_bid["min_acceptance_ratio"] == 1
    op_time = pp.get_operation_time(t)
    assert op_time < 0
    assert s_bid["price"] == pytest.approx(
        calculate_EOM_price_if_off(
            unit=pp,
            marginal_cost_inflex=pp.calculate_marginal_cost(t, 40),
            bid_quantity_inflex=40,
            op_time=op_time,
        )
    )
    # start-up markup on top of the min-load cost
    assert s_bid["price"] > COST_M / 40

    f_bid = _bid(bids, bid_type="LB")
    assert f_bid["parent_bid_id"] == s_bid["bid_id"]
    assert f_bid["volume"] == {t: pytest.approx(60)}
    assert f_bid["price"] == pytest.approx((COST_U - COST_M) / 60)
    assert f_bid.get("min_acceptance_ratio") is None
    for b in bids:
        assert b["start_time"] == t and b["end_time"] == t + HOUR
        assert b["only_hours"] is None and b["node"] == pp.node


def test_off_case_flex_bid_is_ramp_limited():
    pp, strategy = _ccgt(ramp_up=70)
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    f_bid = _bid(bids, bid_type="LB")
    assert f_bid["volume"] == {t: pytest.approx(30)}
    assert f_bid["price"] == pytest.approx(pp.calculate_chord_cost(t, 40, 70))
    # the start bid (min load) is not ramp-limited
    assert _bid(bids, bid_type="BB")["volume"] == {t: 40}


def test_on_a_increase_and_partial_decrease():
    # running only 3 h, min operating time 4 h: no shutdown allowed
    pp, strategy = _ccgt(_running([8, 9, 10]), min_operating_time=4)
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 2
    p_mU = (COST_U - COST_M) / 60

    i_bid = _bid(bids, bid_type=None)
    assert i_bid["volume"] == pytest.approx(30)
    assert i_bid["price"] == pytest.approx(p_mU)

    d_bid = _bid(bids, bid_type="BB")
    assert d_bid["volume"] == {t: pytest.approx(-30)}
    assert d_bid["price"] == pytest.approx(p_mU - 0.01)
    assert d_bid.get("min_acceptance_ratio") is None
    assert d_bid.get("parent_bid_id") is None


def test_on_b_exit_whole_position_without_decrease():
    pp, strategy = _ccgt(_running([8, 9, 10]))
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 2  # I and X only: no D
    p_0U = COST_U / 100

    i_bid = _bid(bids, bid_type=None)
    assert i_bid["volume"] == pytest.approx(30)
    assert i_bid["price"] == pytest.approx(p_0U)

    x_bid = _bid(bids, bid_type="BB")
    assert x_bid["volume"] == {t: pytest.approx(-70)}
    assert x_bid["min_acceptance_ratio"] == 1
    assert x_bid.get("parent_bid_id") is None
    assert x_bid["price"] == pytest.approx(p_0U - 0.01)
    assert not any("_D_" in str(b.get("bid_id")) for b in bids)


def test_on_c_decrease_with_linked_exit():
    pp, strategy = _ccgt(_running([8, 9, 10]), start_cost=R_HOT)
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 3
    p_mU = (COST_U - COST_M) / 60
    p_0m = (COST_M - R_HOT) / 40

    i_bid = _bid(bids, bid_type=None)
    assert i_bid["volume"] == pytest.approx(30)
    assert i_bid["price"] == pytest.approx(p_mU)

    d_bid = _bid(bids, bid_type="BB")
    assert d_bid["bid_id"] == "ccgt_D_20230701T10"
    assert d_bid["volume"] == {t: pytest.approx(-30)}
    assert d_bid["price"] == pytest.approx(p_mU - 0.01)
    assert d_bid.get("min_acceptance_ratio") is None

    x_bid = _bid(bids, bid_type="LB")
    assert x_bid["parent_bid_id"] == d_bid["bid_id"]
    assert x_bid["volume"] == {t: pytest.approx(-40)}
    assert x_bid["min_acceptance_ratio"] == 1
    assert x_bid["price"] == pytest.approx(p_0m - 0.01)


def test_on_c_at_min_load_exit_is_plain_block():
    pp, strategy = _ccgt(_running([8, 9, 10], volume=40), start_cost=R_HOT)
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 2  # I and X, no D
    assert _bid(bids, bid_type=None)["volume"] == pytest.approx(60)
    x_bid = _bid(bids, bid_type="BB")
    assert x_bid["volume"] == {t: pytest.approx(-40)}
    assert x_bid["min_acceptance_ratio"] == 1
    assert x_bid.get("parent_bid_id") is None
    assert x_bid["price"] == pytest.approx((COST_M - R_HOT) / 40 - 0.01)


def test_on_a_at_min_load_only_increase():
    pp, strategy = _ccgt(_running([8, 9, 10], volume=40), min_operating_time=4)
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 1
    assert bids[0]["volume"] == pytest.approx(60)
    assert bids[0]["price"] == pytest.approx((COST_U - COST_M) / 60)


def test_at_max_no_increase():
    t = INDEX[10]
    # on-c at U: D and linked X only
    pp, strategy = _ccgt(_running([8, 9, 10], volume=100), start_cost=R_HOT)
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert all(_total(b["volume"]) < 0 for b in bids)
    assert _bid(bids, bid_type="BB")["volume"] == {t: pytest.approx(-60)}
    assert _bid(bids, bid_type="LB")["volume"] == {t: pytest.approx(-40)}
    # on-b at U: only X of the whole position
    pp, strategy = _ccgt(_running([8, 9, 10], volume=100))
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert len(bids) == 1
    assert bids[0]["volume"] == {t: pytest.approx(-100)}
    assert bids[0]["min_acceptance_ratio"] == 1


def test_increase_is_ramp_limited_but_buys_are_not():
    pp, strategy = _ccgt(_running([8, 9, 10]), ramp_up=10, min_operating_time=4)
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    i_bid = _bid(bids, bid_type=None)
    assert i_bid["volume"] == pytest.approx(10)
    # switch price uses the ramp-limited max (80 MW)
    p_mU = (pp.calculate_cost(t, 80) - pp.calculate_cost(t, 40)) / 40
    assert i_bid["price"] == pytest.approx(p_mU)
    d_bid = _bid(bids, bid_type="BB")
    assert d_bid["volume"] == {t: pytest.approx(-30)}


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"start_cost": R_HOT},
        {"min_operating_time": 4},
        {"start_cost": R_HOT, "ramp_up": 20},
    ],
)
@pytest.mark.parametrize("volume", [40, 55, 70, 100])
def test_no_self_cross(kwargs, volume):
    hours = list(range(8, 14))
    pp, strategy = _ccgt({h: volume for h in hours}, **kwargs)
    bids = strategy.calculate_bids(pp, _market_config(), _products(*hours))
    # an hour may have no bids (e.g. just started at min load with a ramp limit)
    assert bids
    for t in INDEX[8:14]:
        hour_bids = [b for b in bids if b["start_time"] == t]
        buys = [b["price"] for b in hour_bids if _total(b["volume"]) < 0]
        sells = [b["price"] for b in hour_bids if _total(b["volume"]) > 0]
        if buys and sells:
            assert max(buys) < min(sells)


def test_exit_suppressed_by_min_operating_time():
    pp, strategy = _ccgt(_running([9, 10]), min_operating_time=3)
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    buys = [b for b in bids if _total(b["volume"]) < 0]
    # on-a: only the partial decrease to min load
    assert len(buys) == 1
    assert buys[0]["volume"] == {INDEX[10]: pytest.approx(-30)}
    assert buys[0].get("min_acceptance_ratio") is None


def test_exit_suppressed_by_upcoming_position():
    # min down time 3: shutting down at 10 needs 11 and 12 free
    pp, strategy = _ccgt(_running([8, 9, 10, 12]), min_down_time=3)
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    buys = [b for b in bids if _total(b["volume"]) < 0]
    assert len(buys) == 1
    assert buys[0]["volume"] == {INDEX[10]: pytest.approx(-30)}

    # without the position at 12 the shutdown is allowed (on-b: X of 70)
    pp, strategy = _ccgt(_running([8, 9, 10]), min_down_time=3)
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    buys = [b for b in bids if _total(b["volume"]) < 0]
    assert len(buys) == 1
    assert buys[0]["volume"] == {INDEX[10]: pytest.approx(-70)}
    assert buys[0]["min_acceptance_ratio"] == 1


def test_correction_bid_precedes_economic_bids():
    # over-committed beyond U: forced buy of 20, then bids from P_c = U = 100
    pp, strategy = _ccgt({8: 70, 9: 70, 10: 120})
    t = INDEX[10]
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert bids[0]["volume"] == pytest.approx(-20)
    assert bids[0]["price"] == 3000
    assert len(bids) == 2
    assert bids[1]["volume"] == {t: pytest.approx(-100)}

    # between 0 and min load: forced sell up to m, then bids from P_c = m
    pp, strategy = _ccgt({8: 70, 9: 70, 10: 20})
    bids = strategy.calculate_bids(pp, _market_config(), _products(10))
    assert bids[0]["volume"] == pytest.approx(20)
    assert bids[0]["price"] == -500
    assert bids[1]["volume"] == pytest.approx(60)  # I from 40 to 100


def test_first_index_hour():
    t = INDEX[0]
    # off at the first hour: look-back default treats the unit as running
    pp, strategy = _ccgt()
    op_time = pp.get_operation_time(t)
    assert op_time > 0
    bids = strategy.calculate_bids(pp, _market_config(), _products(0, 1))
    s_bid = _bid(bids, bid_id="ccgt_S_20230701T00")
    assert s_bid["price"] == pytest.approx(
        calculate_EOM_price_if_on(
            unit=pp,
            market_id="ID",
            start=t,
            marginal_cost_flex=pp.calculate_marginal_cost(t, 40),
            bid_quantity_inflex=40,
            foresight=strategy.foresight,
        )
    )
    # running at the first hour: shutdown allowed by the look-back default
    pp, strategy = _ccgt({0: 70}, min_operating_time=1)
    bids = strategy.calculate_bids(pp, _market_config(), _products(0))
    x_bid = _bid(bids, bid_type="BB")
    assert x_bid["volume"] == {t: pytest.approx(-70)}


def test_running_and_down_times_follow_committed_positions():
    # position 8-10, min down time 3; products 10..14
    pp, strategy = _ccgt(_running([8, 9, 10]), min_down_time=3)
    bids = strategy.calculate_bids(pp, _market_config(), _products(10, 11, 12, 13, 14))
    hours = {b["start_time"] for b in bids}
    # 10: on bids; 11: off after running (start bid with stay-on discount);
    # 12 and 13: min down time not over along the committed path -> nothing;
    # 14: off for 3 h -> start bid again
    assert hours == {INDEX[10], INDEX[11], INDEX[14]}
    s_11 = _bid(bids, bid_id="ccgt_S_20230701T11")
    assert s_11["price"] == pytest.approx(
        calculate_EOM_price_if_on(
            unit=pp,
            market_id="ID",
            start=INDEX[11],
            marginal_cost_flex=pp.calculate_marginal_cost(INDEX[11], 40),
            bid_quantity_inflex=40,
            foresight=strategy.foresight,
        )
    )
    s_14 = _bid(bids, bid_id="ccgt_S_20230701T14")
    assert s_14["price"] == pytest.approx(
        calculate_EOM_price_if_off(
            unit=pp,
            marginal_cost_inflex=pp.calculate_marginal_cost(INDEX[14], 40),
            bid_quantity_inflex=40,
            op_time=-3,
        )
    )


def test_non_energy_product_falls_back_to_flexable():
    pp, strategy = _ccgt()
    config = _market_config()
    config.product_type = "capacity_pos"
    # no correction or switch-price bids; behaves like the flexable strategy
    bids = strategy.calculate_bids(pp, config, _products(10))
    assert all(b.get("bid_type") is None for b in bids)


# =============================================================================
# clearing round trip
# =============================================================================


def _wind(position, id_availability):
    forecaster = PowerplantForecaster(
        INDEX,
        availability=1,
        availability_forecast={"ID": id_availability},
        fuel_prices={"renewable": 0, "co2": 0},
        market_prices={"ID": 0},
    )
    wind = PowerPlant(
        id="wind",
        unit_operator="op",
        technology="wind onshore",
        bidding_strategies={"ID": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=200,
        min_power=0,
        efficiency=1,
        fuel_type="renewable",
        forecaster=forecaster,
    )
    wind.outputs["energy"].loc[INDEX[0] : INDEX[-1]] = position
    return wind


def _demand(position, id_demand):
    forecaster = DemandForecaster(
        INDEX, demand=position, demand_forecast={"ID": id_demand}
    )
    demand = Demand(
        id="demand",
        unit_operator="op",
        technology="inflex_demand",
        bidding_strategies={"ID": DemandEnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=-1000,
        min_power=0,
        price=3000,
        forecaster=forecaster,
    )
    demand.outputs["energy"].loc[INDEX[0] : INDEX[-1]] = position
    return demand


@pytest.mark.parametrize("start_cost", [0.0, R_HOT])
def test_clearing_round_trip_never_below_min_load(start_cost):
    config = _market_config()
    products = _products(10, 11)
    thermal, _ = _ccgt(_running(range(6, 12)), start_cost=start_cost)
    # wind can sell 80 MW more in hour 10 but only 20 MW more in hour 11
    availability = [0.6] * 24
    availability[10] = 0.65
    availability[11] = 0.35
    wind = _wind(position=50, id_availability=availability)
    demand = _demand(position=-120, id_demand=-130)  # buys 10 MW more
    units = [thermal, wind, demand]

    orderbook = []
    for unit in units:
        for i, bid in enumerate(unit.calculate_bids(config, products)):
            bid["agent_addr"] = unit.id
            if bid.get("bid_id") is None:
                bid["bid_id"] = f"{unit.id}_{i}"
            bid["bid_type"] = bid.get("bid_type") or "SB"
            orderbook.append(bid)
    assert {b["agent_addr"] for b in orderbook} == {"ccgt", "wind", "demand"}

    role = ComplexClearingRole(config)
    accepted, rejected, meta, _ = role.clear(orderbook, products)

    net = {(u.id, t): u.outputs["energy"].at[t] for u in units for t, _, _ in products}
    for order in accepted:
        volumes = order["accepted_volume"]
        if not isinstance(volumes, dict):
            volumes = {order["start_time"]: volumes}
        for t, v in volumes.items():
            net[(order["agent_addr"], t)] += v

    for (unit_id, t), value in net.items():
        unit = next(u for u in units if u.id == unit_id)
        if isinstance(unit, PowerPlant):
            assert not (1e-4 < value < unit.min_power - 1e-4), (unit_id, t, value)

    t10, t11 = INDEX[10], INDEX[11]
    # hour 10: enough cheap wind to buy the thermal unit out completely
    assert net[("ccgt", t10)] == pytest.approx(0, abs=1e-4)
    # hour 11: no exit; on-c reduces partly (D), on-b keeps the whole position
    if start_cost:
        assert net[("ccgt", t11)] == pytest.approx(60, abs=1e-4)
    else:
        assert net[("ccgt", t11)] == pytest.approx(70, abs=1e-4)

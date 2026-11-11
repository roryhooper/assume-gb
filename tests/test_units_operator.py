# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime

import pandas as pd
import pytest
from dateutil import rrule as rr
from dateutil.relativedelta import relativedelta as rd
from mango import RoleAgent, activate, create_tcp_container
from mango.util.clock import ExternalClock
from mango.util.termination_detection import tasks_complete_or_sleeping

from assume.common.fast_pandas import FastIndex
from assume.common.forecaster import DemandForecaster, PowerplantForecaster
from assume.common.market_objects import MarketConfig, MarketProduct
from assume.common.units_operator import UnitsOperator
from assume.common.utils import datetime2timestamp
from assume.strategies.naive_strategies import EnergyNaiveStrategy
from assume.strategies.portfolio_strategies import (
    UnitsOperatorDirectStrategy,
)
from assume.units.demand import Demand
from assume.units.powerplant import PowerPlant

start = datetime(2020, 1, 1)
end = datetime(2020, 12, 2)


@pytest.fixture
async def units_operator() -> UnitsOperator:
    market_id = "EOM"
    marketconfig = MarketConfig(
        market_id=market_id,
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=rd(hours=1),
        market_mechanism="pay_as_clear",
        market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
    )
    clock = ExternalClock(0)
    container = create_tcp_container(addr=("0.0.0.0", 9098), clock=clock)
    units_agent = RoleAgent()
    units_role = UnitsOperator(available_markets=[marketconfig])
    units_agent.add_role(units_role)
    agent_id = container.register(units_agent)

    index = FastIndex(start=start, end=end + pd.Timedelta(hours=4), freq="1h")

    params_dict = {
        "bidding_strategies": {"EOM": EnergyNaiveStrategy()},
        "technology": "energy",
        "unit_operator": agent_id,
        "max_power": -1000,
        "min_power": 0,
        "forecaster": DemandForecaster(index, market_prices={"EOM": 50}, demand=-1000),
    }
    unit = Demand("testdemand", **params_dict)
    units_role.add_unit(unit)

    start_ts = datetime2timestamp(start)
    clock.set_time(start_ts)

    async with activate(container):
        yield units_role
        end_ts = datetime2timestamp(end)
        clock.set_time(end_ts)
        await tasks_complete_or_sleeping(container)


async def test_set_unit_dispatch(units_operator: UnitsOperator):
    orderbook = [
        {
            "start_time": start,
            "end_time": start + rd(hours=1),
            "volume": 500,
            "accepted_volume": 500,
            "price": 1000,
            "accepted_price": 1000,
            "agent_addr": "gen1",
            "unit_id": "testdemand",
            "only_hours": None,
        }
    ]
    marketconfig = units_operator.available_markets[0]

    assert units_operator.units["testdemand"].outputs["energy"].max() == 0

    units_operator.set_unit_dispatch(orderbook, marketconfig)
    assert units_operator.units["testdemand"].outputs["energy"].max() == 500


async def test_write_actual_dispatch(units_operator: UnitsOperator):
    units_operator.write_actual_dispatch("energy")
    assert units_operator.last_sent_dispatch["energy"] > 0
    assert units_operator.last_sent_dispatch["test"] == 0
    units_operator.write_actual_dispatch("test")
    assert units_operator.last_sent_dispatch["test"] > 0


async def test_independent_bids_portfolio(units_operator: UnitsOperator):
    marketconfig = units_operator.available_markets[0]
    from assume.common.utils import get_available_products

    products = get_available_products(marketconfig.market_products, start)
    strategy = UnitsOperatorDirectStrategy()
    orderbook = strategy.calculate_bids(units_operator, marketconfig, products)
    assert len(orderbook) == 1

    assert orderbook[0]["volume"] == -1000
    assert orderbook[0]["price"] == 3000


async def test_get_actual_dispatch(units_operator: UnitsOperator):
    # GIVEN the first hour happened
    # the UnitOperator does not
    clock = units_operator.context.context.clock

    last = clock.time
    clock.set_time(clock.time + 3600)
    # WHEN actual_dispatch is called
    market_dispatch, unit_dfs = units_operator.get_actual_dispatch("energy", last)
    # THEN resulting unit dispatch dataframe contains one row
    # which is for the current time - as we must know our current dispatch
    assert datetime2timestamp(unit_dfs[0]["time"][0]) == clock.time
    assert len(unit_dfs[0]["time"]) == 1
    assert len(market_dispatch) == 0

    # WHEN another hour passes
    last = clock.time
    clock.set_time(clock.time + 3600)

    # THEN resulting unit dispatch dataframe contains only one row with current dispatch
    market_dispatch, unit_dfs = units_operator.get_actual_dispatch("energy", last)
    assert datetime2timestamp(unit_dfs[0]["time"][0]) == clock.time
    assert len(unit_dfs[0]["time"]) == 1
    assert len(market_dispatch) == 0

    last = clock.time
    clock.set_time(clock.time + 3600)

    market_dispatch, unit_dfs = units_operator.get_actual_dispatch("energy", last)
    assert datetime2timestamp(unit_dfs[0]["time"][0]) == clock.time
    assert len(unit_dfs[0]["time"]) == 1
    assert len(market_dispatch) == 0


@pytest.fixture
async def pp_operator() -> UnitsOperator:
    marketconfig = MarketConfig(
        market_id="DA",
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=rd(hours=1),
        market_mechanism="pay_as_clear",
        market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
    )
    clock = ExternalClock(0)
    container = create_tcp_container(addr=("0.0.0.0", 9099), clock=clock)
    units_agent = RoleAgent()
    units_role = UnitsOperator(available_markets=[marketconfig])
    units_agent.add_role(units_role)
    agent_id = container.register(units_agent)

    index = FastIndex(start=start, end=end + pd.Timedelta(hours=4), freq="1h")
    forecaster = PowerplantForecaster(
        index,
        availability=0.8,
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"DA": 50},
    )
    unit = PowerPlant(
        "testpp",
        unit_operator=agent_id,
        technology="hard coal",
        bidding_strategies={"DA": EnergyNaiveStrategy()},
        index=index,
        max_power=100,
        min_power=0,
        efficiency=0.5,
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
    )
    units_role.add_unit(unit)
    clock.set_time(datetime2timestamp(start))

    async with activate(container):
        yield units_role
        clock.set_time(datetime2timestamp(end))
        await tasks_complete_or_sleeping(container)


def _set_position(unit, t, volume):
    unit.outputs["energy"].at[t] = volume
    unit.outputs["energy_DA"].at[t] = volume
    unit.outputs["energy_profit_DA"].at[t] = 5.0
    unit.outputs["profit"].at[t] = 7.0


async def test_energy_deviation_written(pp_operator: UnitsOperator):
    unit = pp_operator.units["testpp"]
    clock = pp_operator.context.context.clock
    t1 = start + rd(hours=1)
    _set_position(unit, t1, 100)

    last = clock.time
    clock.set_time(clock.time + 3600)
    pp_operator.get_actual_dispatch("energy", last)

    # availability caps physical output at 80 -> deviation is 80 - 100
    assert unit.outputs["energy"].at[t1] == 80
    assert unit.outputs["energy_deviation"].at[t1] == -20
    # the market position itself is untouched
    assert unit.outputs["energy_DA"].at[t1] == 100


async def test_deviation_warning(pp_operator: UnitsOperator, caplog):
    unit = pp_operator.units["testpp"]
    clock = pp_operator.context.context.clock
    t2 = start + rd(hours=2)

    # no position -> no deviation -> silent
    last = clock.time
    clock.set_time(clock.time + 3600)
    with caplog.at_level("WARNING", logger="assume.common.units_operator"):
        pp_operator.get_actual_dispatch("energy", last)
    assert "could not deliver" not in caplog.text

    # position above availability -> one aggregated warning for the unit
    _set_position(unit, t2, 100)
    last = clock.time
    clock.set_time(clock.time + 3600)
    with caplog.at_level("WARNING", logger="assume.common.units_operator"):
        pp_operator.get_actual_dispatch("energy", last)
    msgs = [r.getMessage() for r in caplog.records if "could not deliver" in r.message]
    assert len(msgs) == 1
    assert "testpp" in msgs[0] and "20.00 MWh" in msgs[0]


async def test_dispatch_export_includes_profit_and_deviation_not_strategy_profit(
    pp_operator: UnitsOperator,
):
    unit = pp_operator.units["testpp"]
    clock = pp_operator.context.context.clock
    _set_position(unit, start + rd(hours=1), 100)

    last = clock.time
    clock.set_time(clock.time + 3600)
    _, unit_dispatch = pp_operator.get_actual_dispatch("energy", last)

    keys = set(unit_dispatch[0].keys())
    assert "energy_profit_DA" in keys
    assert "energy_deviation" in keys
    assert "profit" not in keys


async def test_on_stop_flushes_final_dispatch(pp_operator: UnitsOperator):
    unit = pp_operator.units["testpp"]
    clock = pp_operator.context.context.clock
    t1 = start + rd(hours=1)
    pp_operator.valid_orders["energy"].append(
        {
            "start_time": t1,
            "end_time": t1 + rd(hours=1),
            "volume": 100,
            "accepted_volume": 100,
            "price": 10,
            "accepted_price": 10,
            "agent_addr": "x",
            "market_id": "DA",
            "unit_id": "testpp",
            "only_hours": None,
        }
    )
    _set_position(unit, t1, 100)
    clock.set_time(clock.time + 2 * 3600)

    sent = []
    pp_operator.context.schedule_instant_message = lambda **kw: sent.append(kw)
    pp_operator.context.data["output_agent_addr"] = "out"

    await pp_operator.on_stop()

    assert any(m["content"]["type"] == "unit_dispatch" for m in sent)
    assert unit.outputs["energy_deviation"].at[t1] == -20


def test_participate():
    """
    Tests that an operator without units does not participate.
    And an operator with units for the wrong market does not participate.
    A correct units operator participates correctly.
    """
    market_id = "EOM"
    marketconfig = MarketConfig(
        market_id=market_id,
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=rd(hours=1),
        market_mechanism="pay_as_clear",
        market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
    )
    clock = ExternalClock(0)
    units_role = UnitsOperator(available_markets=[marketconfig])

    index = FastIndex(start=start, end=end + pd.Timedelta(hours=4), freq="1h")

    assert not units_role.participate(marketconfig)

    params_dict = {
        "bidding_strategies": {"wrong_market": EnergyNaiveStrategy()},
        "technology": "energy",
        "unit_operator": "x",
        "max_power": -1000,
        "min_power": 0,
        "forecaster": DemandForecaster(index, demand=-1000),
    }
    unit = Demand("testdemand", **params_dict)
    units_role.add_unit(unit)

    assert not units_role.participate(marketconfig)

    params_dict = {
        "bidding_strategies": {"EOM": EnergyNaiveStrategy()},
        "technology": "energy",
        "unit_operator": "x",
        "max_power": -1000,
        "min_power": 0,
        "forecaster": DemandForecaster(index, demand=-1000),
    }
    unit = Demand("testdemand", **params_dict)
    units_role.add_unit(unit)

    assert units_role.participate(marketconfig)


def test_participate_lambda():
    """
    Tests that one of the selected lambda functions works correctly in the participation
    """
    market_id = "EOM"
    marketconfig = MarketConfig(
        market_id=market_id,
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=rd(hours=1),
        market_mechanism="pay_as_clear",
        market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
        eligible_obligations_lambda="only_renewables",
    )
    units_role = UnitsOperator(available_markets=[marketconfig])
    index = FastIndex(start=start, end=end + pd.Timedelta(hours=4), freq="1h")

    assert not units_role.participate(marketconfig)

    params_dict = {
        "bidding_strategies": {"EOM": EnergyNaiveStrategy()},
        "technology": "energy",
        "unit_operator": "x",
        "max_power": 10,
        "min_power": 0,
        "forecaster": PowerplantForecaster(index),
    }
    unit = PowerPlant("testdemand", **params_dict)
    units_role.add_unit(unit)
    assert not units_role.participate(marketconfig)

    params_dict = {
        "bidding_strategies": {"EOM": EnergyNaiveStrategy()},
        "technology": "wind offshore",
        "unit_operator": "x",
        "max_power": 1000,
        "min_power": 0,
        "forecaster": PowerplantForecaster(index),
    }
    unit = PowerPlant("testdemand", **params_dict)
    units_role.add_unit(unit)

    assert units_role.participate(marketconfig)


def test_participate_custom_lambda():
    """
    Tests that the custom lambda function is respected in the participation
    """
    market_id = "EOM"
    marketconfig = MarketConfig(
        market_id=market_id,
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=rd(hours=1),
        market_mechanism="pay_as_clear",
        market_products=[MarketProduct(rd(hours=1), 1, rd(hours=1))],
        eligible_obligations_lambda=lambda u: abs(u.get("max_power", 0)) > 100,
    )
    units_role = UnitsOperator(available_markets=[marketconfig])
    index = FastIndex(start=start, end=end + pd.Timedelta(hours=4), freq="1h")

    assert not units_role.participate(marketconfig)

    params_dict = {
        "bidding_strategies": {"EOM": EnergyNaiveStrategy()},
        "technology": "energy",
        "unit_operator": "x",
        "max_power": 10,
        "min_power": 0,
        "forecaster": PowerplantForecaster(index),
    }
    unit = PowerPlant("testdemand", **params_dict)
    units_role.add_unit(unit)
    assert not units_role.participate(marketconfig)

    params_dict = {
        "bidding_strategies": {"EOM": EnergyNaiveStrategy()},
        "technology": "energy",
        "unit_operator": "x",
        "max_power": 1000,
        "min_power": 0,
        "forecaster": PowerplantForecaster(index),
    }
    unit = PowerPlant("testdemand", **params_dict)
    units_role.add_unit(unit)

    assert units_role.participate(marketconfig)

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
from assume.common.forecaster import DemandForecaster
from assume.common.market_objects import MarketConfig, MarketProduct
from assume.common.units_operator import UnitsOperator
from assume.common.utils import datetime2timestamp
from assume.strategies.naive_strategies import EnergyNaiveStrategy
from assume.strategies.portfolio_strategies import UnitsOperatorDirectStrategy
from assume.units.sequential_units import SequentialDemand

start = datetime(2020, 1, 1)
end = datetime(2020, 1, 3)
H = rd(hours=1)


def make_market(market_id, product_type, param_dict=None):
    return MarketConfig(
        market_id=market_id,
        opening_hours=rr.rrule(rr.HOURLY, dtstart=start, until=end),
        opening_duration=H,
        market_mechanism="pay_as_clear",
        market_products=[MarketProduct(H, 1, H)],
        product_type=product_type,
        param_dict=param_dict or {},
    )


@pytest.fixture
async def units_operator() -> UnitsOperator:
    bal = make_market("BAL", "balancing_energy", {"imbalance_settlement": True})
    da = make_market("DA", "energy")
    other = make_market("OTHER", "balancing_energy")
    clock = ExternalClock(0)
    container = create_tcp_container(addr=("0.0.0.0", 9099), clock=clock)
    agent = RoleAgent()
    role = UnitsOperator(available_markets=[])
    agent.add_role(role)
    agent_id = container.register(agent)

    index = FastIndex(start=start, end=end + pd.Timedelta(hours=4), freq="1h")
    unit = SequentialDemand(
        "seqdemand",
        unit_operator=agent_id,
        technology="energy",
        bidding_strategies={m: EnergyNaiveStrategy() for m in ("BAL", "DA", "OTHER")},
        max_power=-1000,
        min_power=0,
        forecaster=DemandForecaster(index, market_prices={"BAL": 50}, demand=-1000),
    )
    role.add_unit(unit)
    unit.outputs["energy"].at[start] = -100

    clock.set_time(datetime2timestamp(start))
    async with activate(container):
        # set after activation, as markets without address cannot be registered
        role.available_markets = [bal, da, other]
        role.registered_markets = {m.market_id: m for m in (bal, da, other)}
        role.portfolio_strategies = {
            m.market_id: UnitsOperatorDirectStrategy() for m in (bal, da, other)
        }
        yield role
        clock.set_time(datetime2timestamp(end))
        await tasks_complete_or_sleeping(container)


def notification(market_id):
    return {
        "start_time": start,
        "end_time": start + H,
        "volume": 0,
        "price": 0,
        "accepted_volume": 0,
        "accepted_price": 200,
        "fpn": -110,
        "position": -100,
        "agent_addr": "gen1",
        "unit_id": "seqdemand",
        "only_hours": None,
    }


async def test_handle_feedback_settles(units_operator: UnitsOperator):
    content = {
        "context": "clearing",
        "market_id": "BAL",
        "accepted_orders": [],
        "rejected_orders": [notification("BAL")],
    }
    units_operator.handle_market_feedback(content, {})
    out = units_operator.units["seqdemand"].outputs
    assert out["imbalance"].at[start] == -10
    assert out["imbalance_cashflow"].at[start] == -2000
    assert out["physical"].at[start] == -110


async def test_non_settlement_market_does_not_settle(units_operator: UnitsOperator):
    content = {
        "context": "clearing",
        "market_id": "OTHER",
        "accepted_orders": [],
        "rejected_orders": [notification("OTHER")],
    }
    units_operator.handle_market_feedback(content, {})
    out = units_operator.units["seqdemand"].outputs
    assert out["imbalance"].at[start] == 0


async def test_sequential_mode_suppresses_other_dispatch_writes(
    units_operator: UnitsOperator,
):
    sent = []
    units_operator.context.data["output_agent_addr"] = "out"
    units_operator.context.schedule_instant_message = lambda **kw: sent.append(
        kw["content"]
    )
    clock = units_operator.context.context.clock
    clock.set_time(datetime2timestamp(start) + 3600)
    order = notification("DA")
    order["accepted_volume"] = -100
    order["accepted_price"] = 50
    content = {
        "context": "clearing",
        "market_id": "DA",
        "accepted_orders": [order],
        "rejected_orders": [],
    }
    units_operator.handle_market_feedback(content, {})
    types = [m["type"] for m in sent]
    assert "market_dispatch" in types
    assert "unit_dispatch" not in types
    market_dispatch = next(m for m in sent if m["type"] == "market_dispatch")
    assert len(market_dispatch["data"]) > 0
    # order ended at start + 1h <= now, so it is pruned
    assert units_operator.valid_orders["energy"] == []


async def test_settlement_market_writes_dispatch(units_operator: UnitsOperator):
    units_operator.context.context.clock.set_time(datetime2timestamp(start) + 3600)
    content = {
        "context": "clearing",
        "market_id": "BAL",
        "accepted_orders": [],
        "rejected_orders": [notification("BAL")],
    }
    units_operator.handle_market_feedback(content, {})
    assert units_operator.last_sent_dispatch["balancing_energy"] > 0
    _, unit_dispatch = units_operator.get_actual_dispatch(
        "balancing_energy", datetime2timestamp(start)
    )
    for key in ("profit", "imbalance", "physical", "fpn", "position"):
        assert key in unit_dispatch[0]

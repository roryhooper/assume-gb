# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

from dateutil import rrule as rr

from assume.common.market_objects import MarketConfig, MarketProduct
from assume.markets.clearing_algorithms import clearing_mechanisms
from assume.markets.clearing_algorithms.balancing import BalancingPayAsClearRole

START = datetime(2005, 6, 1)
END = START + timedelta(hours=1)
PRODUCT = (START, END, None)

bal_config = MarketConfig(
    market_id="BAL",
    market_products=[MarketProduct(timedelta(hours=1), 1, timedelta(hours=1))],
    additional_fields=["fpn", "position"],
    opening_hours=rr.rrule(
        rr.HOURLY, dtstart=START, until=START + timedelta(days=1), cache=True
    ),
    opening_duration=timedelta(hours=1),
    volume_unit="MW",
    price_unit="GBP/MWh",
    market_mechanism="balancing_pay_as_clear",
    maximum_bid_price=3000,
    minimum_bid_price=-500,
    maximum_bid_volume=10000,
)


def bal_order(unit, volume, price, fpn, position):
    return {
        "start_time": START,
        "end_time": END,
        "only_hours": None,
        "unit_id": unit,
        "volume": volume,
        "price": price,
        "fpn": fpn,
        "position": position,
    }


def clear(orders):
    role = BalancingPayAsClearRole(bal_config)
    return role.clear(orders, [PRODUCT])


def test_registered():
    assert clearing_mechanisms["balancing_pay_as_clear"] is BalancingPayAsClearRole


def test_balancing_short():
    orders = [
        bal_order("vre", 0, 0, 80, 100),
        bal_order("gas", 50, 90, 0, 0),
        bal_order("gas", 30, 120, 0, 0),
    ]
    accepted, rejected, meta, flows = clear(orders)
    assert len(accepted) == 1
    assert accepted[0]["price"] == 90
    assert accepted[0]["accepted_volume"] == 20
    assert accepted[0]["accepted_price"] == 90
    assert meta[0]["niv"] == -20
    assert meta[0]["price"] == 90
    assert meta[0]["unmet_volume"] == 0
    assert meta[0]["up_volume"] == 20
    assert meta[0]["down_volume"] == 0
    for o in rejected:
        assert o["accepted_volume"] == 0
        assert o["accepted_price"] == 90
    assert len(rejected) == 2  # inc @120 and the notification
    assert flows == []


def test_balancing_long():
    orders = [
        bal_order("vre", 0, 0, 130, 100),
        bal_order("gas", -40, 30, 0, 0),
        bal_order("sto", -50, 10, 0, 0),
    ]
    accepted, rejected, meta, _ = clear(orders)
    assert len(accepted) == 1
    assert accepted[0]["unit_id"] == "gas"
    assert accepted[0]["accepted_volume"] == -30
    assert meta[0]["price"] == 30
    assert meta[0]["niv"] == 30
    assert meta[0]["down_volume"] == 30
    assert meta[0]["up_volume"] == 0
    sto = [o for o in rejected if o["unit_id"] == "sto"][0]
    assert sto["accepted_volume"] == 0
    assert sto["accepted_price"] == 30


def test_balancing_zero_niv_midpoint():
    orders = [
        bal_order("a", 10, 90, 50, 50),
        bal_order("b", -10, 30, 50, 50),
    ]
    accepted, rejected, meta, _ = clear(orders)
    assert accepted == []
    assert meta[0]["price"] == 60
    assert meta[0]["niv"] == 0
    assert all(o["accepted_price"] == 60 for o in rejected)


def test_balancing_crossed_stack_not_netted():
    orders = [
        bal_order("sto", 50, 5, 40, 50),
        bal_order("gas", -50, 55, 50, 50),
    ]
    accepted, rejected, meta, _ = clear(orders)
    assert len(accepted) == 1
    assert accepted[0]["unit_id"] == "sto"
    assert accepted[0]["accepted_volume"] == 10
    assert meta[0]["price"] == 5
    assert rejected[0]["unit_id"] == "gas"
    assert rejected[0]["accepted_volume"] == 0


def test_balancing_unmet_short_uses_cap():
    orders = [
        bal_order("vre", 0, 0, 80, 100),
        bal_order("gas", 5, 90, 0, 0),
    ]
    accepted, _, meta, _ = clear(orders)
    assert accepted[0]["accepted_volume"] == 5
    assert accepted[0]["accepted_price"] == 3000
    assert meta[0]["price"] == 3000
    assert meta[0]["unmet_volume"] == 15


def test_balancing_unmet_long_uses_floor():
    orders = [
        bal_order("vre", 0, 0, 130, 100),
        bal_order("gas", -10, 30, 0, 0),
    ]
    accepted, _, meta, _ = clear(orders)
    assert accepted[0]["accepted_volume"] == -10
    assert meta[0]["price"] == -500
    assert meta[0]["unmet_volume"] == 20


def test_balancing_notification_only():
    orders = [bal_order("d1", 0, 0, -30, -30), bal_order("d2", 0, 0, -10, -10)]
    accepted, rejected, meta, _ = clear(orders)
    assert accepted == []
    assert meta[0]["price"] == 0
    assert meta[0]["niv"] == 0
    assert len(rejected) == 2


def test_balancing_validate_keeps_notification():
    role = BalancingPayAsClearRole(bal_config)
    role.open_auctions.add(PRODUCT)
    orders = [bal_order("d1", 0, 0, -30, -30)]
    role.validate_orderbook(orders, ("addr", "agent"))
    assert len(orders) == 1
    assert orders[0]["fpn"] == -30


def test_balancing_unmet_is_logged(caplog):
    orders = [
        bal_order("vre", 0, 0, 80, 100),
        bal_order("gas", 5, 90, 0, 0),
    ]
    with caplog.at_level(
        "WARNING", logger="assume.markets.clearing_algorithms.balancing"
    ):
        clear(orders)
    assert "BAL" in caplog.text
    assert "unmet" in caplog.text
    assert "15" in caplog.text


def test_balancing_met_is_not_logged(caplog):
    orders = [
        bal_order("vre", 0, 0, 80, 100),
        bal_order("gas", 50, 90, 0, 0),
    ]
    with caplog.at_level(
        "WARNING", logger="assume.markets.clearing_algorithms.balancing"
    ):
        clear(orders)
    assert "unmet" not in caplog.text

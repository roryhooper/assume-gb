# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import logging
import random
from itertools import groupby
from operator import itemgetter

from assume.common.market_objects import MarketConfig, Orderbook
from assume.markets.base_market import MarketRole
from assume.markets.clearing_algorithms.simple import calculate_meta

logger = logging.getLogger(__name__)

TOLERANCE = 1e-6


class BalancingPayAsClearRole(MarketRole):
    """
    TSO-only single-price balancing market.

    The net imbalance volume (NIV) is computed from the ``fpn`` (physical
    notification) and ``position`` fields of the orders, NIV = sum(fpn - position)
    over units. NIV < 0 means the system is short and incs are accepted; NIV > 0
    means it is long and decs are accepted. Incs and decs are never matched.
    All orders receive the single price of the marginal accepted order.
    """

    def __init__(self, marketconfig: MarketConfig):
        super().__init__(marketconfig)

    def clear(
        self, orderbook: Orderbook, market_products
    ) -> tuple[Orderbook, Orderbook, list[dict], list[dict]]:
        market_getter = itemgetter("start_time", "end_time", "only_hours")
        accepted_orders: Orderbook = []
        rejected_orders: Orderbook = []
        meta = []
        max_price = self.marketconfig.maximum_bid_price
        min_price = self.marketconfig.minimum_bid_price
        orderbook.sort(key=market_getter)
        for product, product_orders in groupby(orderbook, market_getter):
            product_orders = list(product_orders)
            if product not in market_products:
                for order in product_orders:
                    order["accepted_volume"] = 0
                    order["accepted_price"] = 0
                rejected_orders.extend(product_orders)
                continue

            # NIV from first order of each unit
            unit_notifs: dict = {}
            for order in product_orders:
                unit_notifs.setdefault(
                    order["unit_id"], (order["fpn"], order["position"])
                )
            niv = sum(fpn - pos for fpn, pos in unit_notifs.values())
            if abs(niv) <= TOLERANCE:
                niv = 0.0

            incs = [o for o in product_orders if o["volume"] > 0]
            decs = [o for o in product_orders if o["volume"] < 0]
            incs.sort(key=lambda o: (o["price"], random.random()))
            decs.sort(key=lambda o: (o["price"], random.random()), reverse=True)

            accepted_incs: Orderbook = []
            accepted_decs: Orderbook = []
            unmet = 0.0

            if niv < 0:
                need = -niv
                price = None
                for order in incs:
                    if need <= TOLERANCE:
                        break
                    take = min(order["volume"], need)
                    order["accepted_volume"] = take
                    accepted_incs.append(order)
                    need -= take
                    price = order["price"]
                if need > TOLERANCE:
                    unmet = need
                    price = max_price
                elif price is None:
                    price = 0
            elif niv > 0:
                need = niv
                price = None
                for order in decs:
                    if need <= TOLERANCE:
                        break
                    take = min(-order["volume"], need)
                    order["accepted_volume"] = -take
                    accepted_decs.append(order)
                    need -= take
                    price = order["price"]
                if need > TOLERANCE:
                    unmet = need
                    price = min_price
                elif price is None:
                    price = 0
            else:
                if incs and decs:
                    price = (incs[0]["price"] + decs[0]["price"]) / 2
                elif incs:
                    price = incs[0]["price"]
                elif decs:
                    price = decs[0]["price"]
                else:
                    price = 0

            if unmet > TOLERANCE:
                logger.warning(
                    "%s: product starting %s has NIV %.2f, unmet volume %.2f "
                    "settled at the price limit",
                    self.marketconfig.market_id,
                    product[0],
                    niv,
                    unmet,
                )

            price = float(price)
            accepted_product = accepted_incs + accepted_decs
            for order in accepted_product:
                order["accepted_price"] = price
            accepted_orders.extend(accepted_product)

            accepted_ids = {id(o) for o in accepted_product}
            for order in product_orders:
                if id(order) not in accepted_ids:
                    order["accepted_volume"] = 0
                    order["accepted_price"] = price
                    rejected_orders.append(order)

            product_meta = calculate_meta(accepted_incs, accepted_decs, product)
            product_meta["price"] = price
            product_meta["max_price"] = price
            product_meta["min_price"] = price
            product_meta["niv"] = niv
            product_meta["unmet_volume"] = unmet
            product_meta["up_volume"] = product_meta["supply_volume"]
            product_meta["down_volume"] = product_meta["demand_volume"]
            meta.append(product_meta)

        flows = []
        return accepted_orders, rejected_orders, meta, flows

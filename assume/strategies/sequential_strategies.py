# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

"""
Bidding strategies for sequential markets (DA -> ID -> BAL -> imbalance settlement).

One strategy instance serves all three markets of a unit. Energy markets bid the
increment towards a target given by the market's forecast vintage; the balancing
market always receives a notification (fpn and position) with every order.
"""

from datetime import datetime

from assume.common.base import BaseStrategy, BaseUnit, MinMaxStrategy
from assume.common.market_objects import MarketConfig, Order, Orderbook, Product

EPS = 1e-9


class SequentialMarketStrategy(BaseStrategy):
    """Base class routing bids by product type and recording realised prices."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bal_markup: float = float(kwargs.get("bal_markup", 0.0))
        self.sellback_price: float = float(kwargs.get("sellback_price", 0.0))
        self.storage_foresight: str = kwargs.get("storage_foresight", "12h")
        self.realised_prices: dict[str, dict[datetime, float]] = {}

    def calculate_bids(
        self,
        unit: BaseUnit,
        market_config: MarketConfig,
        product_tuples: list[Product],
        **kwargs,
    ) -> Orderbook:
        if market_config.product_type == "energy":
            return self.bid_energy(unit, market_config, product_tuples)
        if market_config.product_type == "balancing_energy":
            return self.bid_balancing(unit, market_config, product_tuples)
        return []

    @staticmethod
    def vintage(market_config: MarketConfig) -> str:
        return market_config.param_dict.get("forecast_vintage", "actual")

    def calculate_reward(
        self, unit: BaseUnit, marketconfig: MarketConfig, orderbook: Orderbook
    ) -> None:
        """Record realised clearing prices only; never writes unit outputs."""
        prices = self.realised_prices.setdefault(marketconfig.market_id, {})
        for order in orderbook:
            prices[order["start_time"]] = order["accepted_price"]

    def realised_price(self, market_id: str, t: datetime) -> float | None:
        return self.realised_prices.get(market_id, {}).get(t)

    @staticmethod
    def make_order(
        unit: BaseUnit, product: Product, price: float, volume: float
    ) -> Order:
        return {
            "start_time": product[0],
            "end_time": product[1],
            "only_hours": product[2],
            "price": price,
            "volume": volume,
            "node": unit.node,
        }

    def bid_energy(
        self, unit: BaseUnit, market_config: MarketConfig, product_tuples: list[Product]
    ) -> Orderbook:
        raise NotImplementedError

    def bid_balancing(
        self, unit: BaseUnit, market_config: MarketConfig, product_tuples: list[Product]
    ) -> Orderbook:
        # zero-volume notification orders must reach clearing, so no remove_empty_bids
        bids: Orderbook = []
        for product in product_tuples:
            start = product[0]
            fpn = self.decide_dispatch(unit, start)
            offers = self.balancing_offers(unit, market_config, product, fpn)
            if not offers:
                offers = [self.make_order(unit, product, 0.0, 0.0)]
            position = unit.net_position(start)
            for offer in offers:
                offer["fpn"] = fpn
                offer["position"] = position
            bids.extend(offers)
        return bids

    def balancing_offers(
        self,
        unit: BaseUnit,
        market_config: MarketConfig,
        product: Product,
        fpn: float,
    ) -> Orderbook:
        return []

    def decide_dispatch(self, unit: BaseUnit, start: datetime) -> float:
        raise NotImplementedError

    def on_settlement(self, unit: BaseUnit, start: datetime) -> None:
        return None


class DemandSequentialFixedStrategy(SequentialMarketStrategy, MinMaxStrategy):
    """Buys the vintage demand at the fixed unit price, sells back surplus."""

    def bid_energy(self, unit, market_config, product_tuples):
        vintage = self.vintage(market_config)
        bids = []
        for product in product_tuples:
            t = product[0]
            target = unit.forecaster.get_demand(vintage).at[t]
            delta = target - unit.outputs["energy"].at[t]
            if delta < -EPS:
                bids.append(self.make_order(unit, product, unit.price.at[t], delta))
            elif delta > EPS:
                bids.append(self.make_order(unit, product, self.sellback_price, delta))
        return bids

    def decide_dispatch(self, unit, start):
        return unit.forecaster.get_demand("actual").at[start]


class RenewableSequentialHeuristicStrategy(SequentialMarketStrategy, MinMaxStrategy):
    """Trades towards vintage availability at marginal cost; curtails in BAL."""

    def bid_energy(self, unit, market_config, product_tuples):
        vintage = self.vintage(market_config)
        bids = []
        for product in product_tuples:
            t = product[0]
            target = unit.forecaster.get_availability(vintage).at[t] * unit.max_power
            delta = target - unit.outputs["energy"].at[t]
            if delta > EPS:
                price = unit.calculate_marginal_cost(t, 0)
                bids.append(self.make_order(unit, product, price, delta))
            elif delta < -EPS:
                bids.append(
                    self.make_order(
                        unit, product, market_config.maximum_bid_price, delta
                    )
                )
        return bids

    def decide_dispatch(self, unit, start):
        return unit.forecaster.get_availability("actual").at[start] * unit.max_power

    def balancing_offers(self, unit, market_config, product, fpn):
        if fpn <= EPS:
            return []
        mc = unit.calculate_marginal_cost(product[0], 0)
        return [self.make_order(unit, product, mc - self.bal_markup, -fpn)]

# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

"""
Bidding strategies for sequential markets (DA -> ID -> BAL -> imbalance settlement).

One strategy instance serves all three markets of a unit. Energy markets bid the
increment towards a target given by the market's forecast vintage; the balancing
market always receives a notification (fpn and position) with every order.
"""

import math
from datetime import datetime

import pandas as pd

from assume.common.base import (
    BaseStrategy,
    BaseUnit,
    MinMaxChargeStrategy,
    MinMaxStrategy,
)
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


class PowerplantSequentialHeuristicStrategy(SequentialMarketStrategy, MinMaxStrategy):
    """Sells headroom and buys back committed output above min_power at marginal cost."""

    def bid_energy(self, unit, market_config, product_tuples):
        bids = []
        for product in product_tuples:
            t = product[0]
            # calculate_min_max_power clips its minimum at zero (heat demand
            # floor), so the buy-back towards min_power is derived directly.
            _, max_p = unit.calculate_min_max_power(t, product[1])
            max_p = max_p[0]
            position = unit.outputs["energy"].at[t]
            min_p = unit.min_power - position
            mc = unit.calculate_marginal_cost(t, position)
            if max_p > EPS:
                bids.append(self.make_order(unit, product, mc, max_p))
            if min_p < -EPS:
                bids.append(self.make_order(unit, product, mc, min_p))
        return bids

    @staticmethod
    def _capacity(unit, start):
        return unit.forecaster.availability.at[start] * unit.max_power

    def decide_dispatch(self, unit, start):
        position = unit.outputs["energy"].at[start]
        return min(max(position, unit.min_power), self._capacity(unit, start))

    def balancing_offers(self, unit, market_config, product, fpn):
        t = product[0]
        mc = unit.calculate_marginal_cost(t, fpn)
        offers = []
        inc = self._capacity(unit, t) - fpn
        if inc > EPS:
            offers.append(self.make_order(unit, product, mc + self.bal_markup, inc))
        dec = fpn - unit.min_power
        if dec > EPS:
            offers.append(self.make_order(unit, product, mc - self.bal_markup, -dec))
        return offers


class StorageSequentialHeuristicStrategy(
    SequentialMarketStrategy, MinMaxChargeStrategy
):
    """
    Arbitrages around the value of stored energy and offers SOC-feasible BAL headroom.

    The value v of stored energy is the mean reference price over
    ``±storage_foresight``: the ``DA`` price forecast in DA, realised DA prices
    (falling back to the forecast) in ID and BAL. Energy markets charge at
    ``v·η_ch`` and discharge at ``v/η_dis``. When v is missing or ≤ 0 the
    bands would invert, so that hour gets no bid and no BAL offer. Every volume (energy bids, FPN and
    FPN plus any accepted BAL volume) stays within the power limits and the
    SOC limits, so settlement never has to clip the SOC.
    """

    DA_MARKET = "DA"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.foresight = pd.Timedelta(self.storage_foresight).to_pytimedelta()
        self.last_settled: dict[str, datetime] = {}

    def on_settlement(self, unit, start):
        last = self.last_settled.get(unit.id)
        if last is None or start > last:
            self.last_settled[unit.id] = start

    # --- SOC and power feasibility -------------------------------------------

    @staticmethod
    def power_bounds(unit, soc: float) -> tuple[float, float]:
        """Total (absolute) charge and discharge power feasible for one step at ``soc``."""
        lo = max(unit.max_power_charge, unit.calculate_soc_max_charge(soc))
        hi = min(unit.max_power_discharge, unit.calculate_soc_max_discharge(soc))
        return min(lo, 0.0), max(hi, 0.0)

    @staticmethod
    def next_soc(unit, soc: float, power: float) -> float:
        hours = unit.index.freq / pd.Timedelta(hours=1)
        if power > 0:
            soc -= power * hours / unit.efficiency_discharge / unit.capacity
        elif power < 0:
            soc -= power * hours * unit.efficiency_charge / unit.capacity
        return min(max(soc, unit.min_soc), unit.max_soc)

    def feasible_dispatch(self, unit, soc: float, power: float) -> float:
        lo, hi = self.power_bounds(unit, soc)
        return min(max(power, lo), hi)

    def planned_soc(self, unit, t: datetime) -> float:
        """
        SOC at the start of ``t``.

        Starts from the SOC written by the last settlement (or the initial SOC)
        and rolls it forward over unsettled hours with the position, clipped to
        what is feasible, i.e. the FPN that :meth:`decide_dispatch` would send.
        """
        freq = unit.index.freq
        last = self.last_settled.get(unit.id)
        if last is not None and t <= last:
            return float(unit.outputs["soc"].at[t])
        current = unit.index[0] if last is None else last + freq
        soc = float(unit.outputs["soc"].at[current])
        while current < t:
            power = self.feasible_dispatch(unit, soc, unit.net_position(current))
            soc = self.next_soc(unit, soc, power)
            current += freq
        return soc

    # --- prices --------------------------------------------------------------

    def forecast_price(self, unit, t: datetime) -> float | None:
        prices = getattr(unit.forecaster, "price", None) or {}
        series = prices.get(self.DA_MARKET)
        if series is None or t not in series.index:
            return None
        value = float(series.at[t])
        return None if math.isnan(value) else value

    def reference_price(self, unit, market_config, t: datetime) -> float | None:
        if self.vintage(market_config) != "da":
            realised = self.realised_price(self.DA_MARKET, t)
            if realised is not None and not math.isnan(realised):
                return float(realised)
        return self.forecast_price(unit, t)

    def energy_value(self, unit, market_config, t: datetime) -> float | None:
        freq = unit.index.freq
        current = max(t - self.foresight, unit.index[0])
        end = min(t + self.foresight, unit.index[-1])
        prices = []
        while current <= end:
            price = self.reference_price(unit, market_config, current)
            if price is not None:
                prices.append(price)
            current += freq
        return sum(prices) / len(prices) if prices else None

    # --- bidding -------------------------------------------------------------

    def bid_energy(self, unit, market_config, product_tuples):
        if not product_tuples:
            return []
        bids = []
        soc = self.planned_soc(unit, product_tuples[0][0])
        for product in product_tuples:
            t = product[0]
            position = unit.net_position(t)
            lo, hi = self.power_bounds(unit, soc)
            volume = 0.0
            price = self.reference_price(unit, market_config, t)
            value = self.energy_value(unit, market_config, t)
            # v <= 0 inverts or collapses the charge/discharge bands: no bid
            if price is not None and value is not None and value > 0:
                discharge_price = value / unit.efficiency_discharge
                charge_price = value * unit.efficiency_charge
                # headroom is the SOC-feasible total minus the held position
                if price >= discharge_price and hi - position > EPS:
                    volume = hi - position
                    bids.append(self.make_order(unit, product, discharge_price, volume))
                elif price <= charge_price and lo - position < -EPS:
                    volume = lo - position
                    bids.append(self.make_order(unit, product, charge_price, volume))
            # advance as though the bid is accepted
            dispatch = min(max(position + volume, lo), hi)
            soc = self.next_soc(unit, soc, dispatch)
        return bids

    def decide_dispatch(self, unit, start):
        soc = self.planned_soc(unit, start)
        return self.feasible_dispatch(unit, soc, unit.net_position(start))

    def balancing_offers(self, unit, market_config, product, fpn):
        t = product[0]
        value = self.energy_value(unit, market_config, t)
        if value is None or value <= 0:
            return []
        lo, hi = self.power_bounds(unit, self.planned_soc(unit, t))
        offers = []
        inc = hi - fpn
        if inc > EPS:
            price = value / unit.efficiency_discharge + self.bal_markup
            offers.append(self.make_order(unit, product, price, inc))
        dec = lo - fpn
        if dec < -EPS:
            price = value * unit.efficiency_charge - self.bal_markup
            offers.append(self.make_order(unit, product, price, dec))
        return offers

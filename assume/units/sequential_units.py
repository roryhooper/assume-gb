# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

"""
Unit subclasses for sequential markets (DA -> ID -> BAL -> imbalance settlement).

The financial position (``outputs["energy"]`` from DA+ID and
``outputs["balancing_energy"]`` from BAL) is never clipped or rewritten.
Physical output is written only by :meth:`SequentialUnitMixin.settle`.
"""

import logging
from datetime import datetime, timedelta

import numpy as np

from assume.common.base import BaseUnit
from assume.common.market_objects import MarketConfig, Orderbook
from assume.units.demand import Demand
from assume.units.powerplant import PowerPlant
from assume.units.storage import Storage

logger = logging.getLogger(__name__)


class SequentialUnitMixin:
    """Separates the market position from physical output and settles imbalance."""

    def set_dispatch_plan(
        self, marketconfig: MarketConfig, orderbook: Orderbook
    ) -> None:
        # bypass SOC recomputation / clipping of subclasses
        BaseUnit.set_dispatch_plan(self, marketconfig, orderbook)

    def calculate_cashflow(self, product_type: str, orderbook: Orderbook) -> None:
        BaseUnit.calculate_cashflow(self, product_type, orderbook)
        for order in orderbook:
            market_id = order.get("market_id")
            if market_id is None:
                continue
            start = order["start_time"]
            end_excl = order["end_time"] - self.index.freq
            volume, price = order["accepted_volume"], order["accepted_price"]
            if isinstance(volume, dict):
                cashflow = np.array([float(price[i] * volume[i]) for i in volume])
            else:
                cashflow = float(price * volume)
            self.outputs[f"cashflow_{market_id}"].loc[start:end_excl] += cashflow

    def net_position(self, t: datetime) -> float:
        return float(
            self.outputs["energy"].at[t] + self.outputs["balancing_energy"].at[t]
        )

    def execute_current_dispatch(self, start: datetime, end: datetime) -> np.ndarray:
        start = max(start, self.index[0])
        return self.outputs["physical"].loc[start:end]

    def calculate_generation_cost(
        self, start: datetime, end: datetime, product_type: str
    ) -> None:
        # costs are written once, in settle()
        return None

    def settlement_cost(self, t: datetime, physical: float) -> float:
        return abs(self.calculate_marginal_cost(t, physical) * physical)

    def settle(
        self, t: datetime, fpn: float, accepted_balancing: float, price: float
    ) -> None:
        """
        Book physical output and imbalance at time ``t``.

        Imbalance > 0 is long (paid the price), < 0 is short (pays the price).
        """
        out = self.outputs
        physical = fpn + accepted_balancing
        position = self.net_position(t)
        imbalance = physical - position
        costs = self.settlement_cost(t, physical)

        out["fpn"].at[t] = fpn
        out["physical"].at[t] = physical
        out["position"].at[t] = position
        out["imbalance"].at[t] = imbalance
        out["imbalance_cashflow"].at[t] = imbalance * price
        out["generation_costs"].at[t] = costs
        # same costs under the base unit / dashboard name, not summed twice
        out["energy_generation_costs"].at[t] = costs

        revenue = sum(
            series.at[t] for key, series in out.items() if key.endswith("_cashflow")
        )
        out["profit"].at[t] = revenue - costs


class SequentialPowerPlant(SequentialUnitMixin, PowerPlant):
    """Power plant with separated position and physical output."""


class SequentialDemand(SequentialUnitMixin, Demand):
    """Demand with separated position and physical output."""

    def settlement_cost(self, t: datetime, physical: float) -> float:
        return 0.0


class SequentialStorage(SequentialUnitMixin, Storage):
    """Storage whose SOC follows physical output, written in settle."""

    def settle(
        self, t: datetime, fpn: float, accepted_balancing: float, price: float
    ) -> None:
        super().settle(t, fpn, accepted_balancing, price)
        physical = self.outputs["physical"].at[t]
        next_t = t + self.index.freq
        if next_t not in self.index:
            return
        time_delta = self.index.freq / timedelta(hours=1)
        soc = self.outputs["soc"].at[t]
        if physical > 0:
            delta_soc = (
                -physical * time_delta / self.efficiency_discharge / self.capacity
            )
        elif physical < 0:
            delta_soc = -physical * time_delta * self.efficiency_charge / self.capacity
        else:
            delta_soc = 0.0
        new_soc = soc + delta_soc
        clipped = min(max(new_soc, self.min_soc), self.max_soc)
        if not np.isclose(clipped, new_soc):
            logger.warning(
                "Unit %s: SOC at %s clipped from %.4f to %.4f (physical=%.2f)",
                self.id,
                next_t,
                new_soc,
                clipped,
                physical,
            )
        self.outputs["soc"].at[next_t] = clipped

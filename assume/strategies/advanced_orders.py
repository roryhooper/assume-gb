# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT


from datetime import datetime

import numpy as np

from assume.common.base import SupportsMinMax
from assume.common.market_objects import MarketConfig, Orderbook, Product
from assume.common.utils import parse_duration
from assume.strategies.flexable import (
    EnergyHeuristicFlexableStrategy,
    calculate_EOM_price_if_off,
    calculate_EOM_price_if_on,
)

# tolerance for comparing positions and volumes (MW)
_EPS = 1e-6


class EnergyHeuristicFlexableBlockStrategy(EnergyHeuristicFlexableStrategy):
    """
    A strategy that bids on the EOM-market with block bids.

    Attributes:
        foresight (pandas.Timedelta): The foresight for the EOM-market.

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # check if kwargs contains eom_foresight argument
        self.foresight = parse_duration(kwargs.get("eom_foresight", "12h"))

    def calculate_bids(
        self,
        unit: SupportsMinMax,
        market_config: MarketConfig,
        product_tuples: list[Product],
        **kwargs,
    ) -> Orderbook:
        """
        Calculates block bids for the EOM-market and returns a list of bids consisting of the start time, end time, only hours, price, volume and bid type.

        The bids take the following form:
        One block bid with the minimum acceptance ratio set to 1 spanning the total clearing period.
        It uses the inflexible power and the weighted average price of the inflexible power as the price.
        This price is based on the marginal cost of the inflexible power and the starting costs.
        The starting costs are split across inflexible power and the average operation or down time of the unit depending on the operation status before.
        Additionally, for every hour where the unit is on, a separate flexible bid is created using the flexible power and marginal costs as bidding price.

        Args:
            unit (SupportsMinMax): A unit that the unit operator manages.
            market_config (MarketConfig): A market configuration.
            product_tuples (list[Product]): A list of tuples containing the start and end time of each product.
            kwargs (dict): Additional arguments.

        Returns:
            Orderbook: A list of bids.

        """

        start = product_tuples[0][0]
        end = product_tuples[-1][1]

        previous_power = unit.get_output_before(start)
        min_power_values, max_power_values = unit.calculate_min_max_power(start, end)

        bids = []
        bid_quantity_block = {}
        bid_price_block = []
        op_time = unit.get_operation_time(start)

        for product, min_power, max_power in zip(
            product_tuples, min_power_values, max_power_values
        ):
            start = product[0]
            end = product[1]

            bid_quantity_flex, bid_price_flex = 0, 0
            bid_price_inflex, bid_quantity_inflex = 0, 0

            current_power = unit.outputs["energy"].at[start]

            # =============================================================================
            # Powerplant is either on, or is able to turn on
            # Calculating possible bid amount and cost
            # =============================================================================

            # adjust max_power for ramp speed
            max_power = unit.calculate_ramp(
                op_time, previous_power, max_power, current_power
            )
            # adjust min_power for ramp speed
            min_power = unit.calculate_ramp(
                op_time, previous_power, min_power, current_power
            )

            bid_quantity_inflex = min_power

            # =============================================================================
            # Calculating marginal cost
            # =============================================================================

            marginal_cost_inflex = unit.calculate_marginal_cost(
                start, current_power + bid_quantity_inflex
            )
            marginal_cost_flex = unit.calculate_marginal_cost(
                start, current_power + max_power
            )

            # =============================================================================
            # Calculating possible price
            # =============================================================================
            if op_time > 0:
                bid_price_inflex = calculate_EOM_price_if_on(
                    unit=unit,
                    market_id=market_config.market_id,
                    start=start,
                    marginal_cost_flex=marginal_cost_flex,
                    bid_quantity_inflex=bid_quantity_inflex,
                    foresight=self.foresight,
                )
            else:
                bid_price_inflex = calculate_EOM_price_if_off(
                    unit=unit,
                    marginal_cost_inflex=marginal_cost_inflex,
                    bid_quantity_inflex=bid_quantity_inflex,
                    op_time=op_time,
                )

            if unit.outputs["heat"].at[start] > 0:
                power_loss_ratio = (
                    unit.outputs["power_loss"].at[start]
                    / unit.outputs["heat"].at[start]
                )
            else:
                power_loss_ratio = 0.0

            # Flex-bid price formulation
            if op_time <= -unit.min_down_time or op_time > 0:
                bid_quantity_flex = max_power - bid_quantity_inflex
                bid_price_flex = (1 - power_loss_ratio) * marginal_cost_flex

            # add volume and price to block bid
            bid_quantity_block[product[0]] = bid_quantity_inflex
            if bid_quantity_inflex > 0:
                bid_price_block.append(bid_price_inflex)

            bids.append(
                {
                    "start_time": start,
                    "end_time": end,
                    "only_hours": None,
                    "price": bid_price_flex,
                    "volume": bid_quantity_flex,
                    "bid_type": "SB",
                    "node": unit.node,
                },
            )
            # calculate previous power with planned dispatch (bid_quantity)
            previous_power = bid_quantity_inflex + bid_quantity_flex + current_power
            op_time = max(op_time, 0) + 1 if previous_power > 0 else min(op_time, 0) - 1

        # calculate weighted average of prices
        volume = 0
        price = 0
        for i in range(len(bid_price_block)):
            price += bid_price_block[i] * list(bid_quantity_block.values())[i]
            volume += list(bid_quantity_block.values())[i]
        if volume != 0:
            mean_price = price / volume

            bids.append(
                {
                    "start_time": product_tuples[0][0],
                    "end_time": product_tuples[-1][1],
                    "only_hours": product_tuples[0][2],
                    "price": mean_price,
                    "volume": bid_quantity_block,
                    "bid_type": "BB",
                    "min_acceptance_ratio": 1,
                    "node": unit.node,
                }
            )

        bids = self.remove_empty_bids(bids)

        return bids


class EnergyHeuristicFlexableLinkedStrategy(EnergyHeuristicFlexableStrategy):
    """
    A strategy that bids on the EOM-market with block and linked bids.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # check if kwargs contains eom_foresight argument
        self.foresight = parse_duration(kwargs.get("eom_foresight", "12h"))

    def calculate_bids(
        self,
        unit: SupportsMinMax,
        market_config: MarketConfig,
        product_tuples: list[Product],
        **kwargs,
    ) -> Orderbook:
        """
        Calculates block and linked bids for the EOM-market and returns a list of bids consisting of the start time, end time, only hours, price, volume and bid type.

        The bids take the following form:
        One block bid with the minimum acceptance ratio set to 1 spanning the total clearing period.
        It uses the inflexible power and the weighted average price of the inflexible power as the price.
        This price is based on the marginal cost of the inflexible power and the starting costs.
        The starting costs are split across inflexible power and the average operation or down time of the unit depending on the operation status before.
        Additionally, for every hour where the unit is on, a separate flexible bid is created using the flexible power and marginal costs as bidding price.
        This bids are linked as children to the block bid.

        Args:
            unit (SupportsMinMax): A unit that the unit operator manages.
            market_config (MarketConfig): A market configuration.
            product_tuples (list[Product]): A list of tuples containing the start and end time of each product.
            kwargs (dict): Additional arguments.

        Returns:
            Orderbook: A list of bids.
        """
        start = product_tuples[0][0]
        end = product_tuples[-1][1]

        previous_power = unit.get_output_before(start)
        min_power_values, max_power_values = unit.calculate_min_max_power(start, end)

        bids = []
        bid_quantity_block = {}
        bid_price_block = []
        op_time = unit.get_operation_time(start)

        block_id = unit.id + "_block"

        for product, min_power, max_power in zip(
            product_tuples, min_power_values, max_power_values
        ):
            start = product[0]
            end = product[1]

            bid_quantity_flex, bid_price_flex = 0, 0
            bid_price_inflex, bid_quantity_inflex = 0, 0

            current_power = unit.outputs["energy"].at[start]

            # =============================================================================
            # Powerplant is either on, or is able to turn on
            # Calculating possible bid amount and cost
            # =============================================================================

            # adjust max_power for ramp speed
            max_power = unit.calculate_ramp(
                op_time, previous_power, max_power, current_power
            )
            # adjust min_power for ramp speed
            min_power = unit.calculate_ramp(
                op_time, previous_power, min_power, current_power
            )

            bid_quantity_inflex = min_power

            # =============================================================================
            # Calculating marginal cost
            # =============================================================================

            marginal_cost_inflex = unit.calculate_marginal_cost(
                start, current_power + bid_quantity_inflex
            )
            marginal_cost_flex = unit.calculate_marginal_cost(
                start, current_power + max_power
            )

            # =============================================================================
            # Calculating possible price
            # =============================================================================
            if op_time > 0:
                bid_price_inflex = calculate_EOM_price_if_on(
                    unit=unit,
                    market_id=market_config.market_id,
                    start=start,
                    marginal_cost_flex=marginal_cost_flex,
                    bid_quantity_inflex=bid_quantity_inflex,
                    foresight=self.foresight,
                )
            else:
                bid_price_inflex = calculate_EOM_price_if_off(
                    unit=unit,
                    marginal_cost_inflex=marginal_cost_inflex,
                    bid_quantity_inflex=bid_quantity_inflex,
                    op_time=op_time,
                )

            if unit.outputs["heat"].at[start] > 0:
                power_loss_ratio = (
                    unit.outputs["power_loss"].at[start]
                    / unit.outputs["heat"].at[start]
                )
            else:
                power_loss_ratio = 0.0

            # Flex-bid price formulation
            if op_time <= -unit.min_down_time or op_time > 0:
                bid_quantity_flex = max_power - bid_quantity_inflex
                bid_price_flex = (1 - power_loss_ratio) * marginal_cost_flex

            bid_quantity_block[product[0]] = bid_quantity_inflex
            if bid_quantity_inflex > 0:
                bid_price_block.append(bid_price_inflex)
                parent_id = block_id
            else:
                parent_id = None

            bids.append(
                {
                    "start_time": start,
                    "end_time": end,
                    "only_hours": None,
                    "price": bid_price_flex,
                    "volume": {start: bid_quantity_flex},
                    "bid_type": "LB",
                    "parent_bid_id": parent_id,
                    "node": unit.node,
                },
            )
            # calculate previous power with planned dispatch (bid_quantity)
            previous_power = bid_quantity_inflex + bid_quantity_flex + current_power
            op_time = max(op_time, 0) + 1 if previous_power > 0 else min(op_time, 0) - 1

        # calculate weighted average of prices
        volume = 0
        price = 0
        for i in range(len(bid_price_block)):
            price += bid_price_block[i] * list(bid_quantity_block.values())[i]
            volume += list(bid_quantity_block.values())[i]

        if volume != 0:
            mean_price = price / volume

            bids.append(
                {
                    "start_time": product_tuples[0][0],
                    "end_time": product_tuples[-1][1],
                    "only_hours": product_tuples[0][2],
                    "price": mean_price,
                    "volume": bid_quantity_block,
                    "bid_type": "BB",
                    "min_acceptance_ratio": 1,
                    "bid_id": block_id,
                    "node": unit.node,
                }
            )

        bids = self.remove_empty_bids(bids)

        return bids


def determine_on_case(
    cost_m: float,
    cost_U: float,
    m: float,
    U: float,
    R: float,
    shutdown_allowed: bool,
) -> tuple[str, dict[str, float]]:
    """
    Switch prices and bidding sub-case of a running thermal unit for one hour.

    The hourly profit at the three allowed extremes is linear in the price *p*:
    off ``-R``, min load ``p * m - cost_m``, max ``p * U - cost_U``. The switch
    prices are where the upper envelope of these lines changes.

    Args:
        cost_m (float): Running cost at min load ``m``.
        cost_U (float): Running cost at the available max ``U``.
        m (float): Min load.
        U (float): Available max (ramp-limited).
        R (float): Restart penalty (start-up cost after the minimum down time).
        shutdown_allowed (bool): Whether shutting down in this hour is allowed.

    Returns:
        tuple[str, dict[str, float]]: ``"on-a"`` if shutdown is not allowed,
        ``"on-c"`` if ``p_0m < p_mU`` (min load is best between the two),
        otherwise ``"on-b"``; and the prices ``p_mU = (cost_U - cost_m) / (U - m)``,
        ``p_0U = (cost_U - R) / U`` and ``p_0m = (cost_m - R) / m``.
        With ``U == m`` there is no flexible range and ``p_mU = p_0U``; with
        ``m <= 0`` running at min load always beats a shutdown and ``p_0m = -inf``.
        ``U`` and ``m`` are treated as equal within a tolerance of 1e-6 MW.
    """
    p_0U = (cost_U - R) / U if U > 0 else float("inf")
    p_mU = (cost_U - cost_m) / (U - m) if abs(U - m) >= _EPS else p_0U
    p_0m = (cost_m - R) / m if m > 0 else float("-inf")
    prices = {"p_mU": p_mU, "p_0U": p_0U, "p_0m": p_0m}

    if not shutdown_allowed:
        return "on-a", prices
    if p_0m < p_mU:
        return "on-c", prices
    return "on-b", prices


class EnergyHeuristicFlexableHourlyLinkedStrategy(EnergyHeuristicFlexableStrategy):
    """
    Thermal strategy for sequential energy markets with hourly block and linked bids.

    Per product hour, measured from the corrected position ``P_c`` (the position after
    any forced correction, see :meth:`BaseStrategy.calculate_correction_bids`):

    - **Off** (``P_c == 0``): a start bid **S** (single-hour block, min load ``m``,
      all-or-nothing) priced at the min-load cost with flexable's start-up markup or
      stay-on discount, and a flexible bid **F** (``U - m``, linked to S) at the chord
      cost ``calculate_chord_cost(m, U)``. Nothing while the minimum down time runs.
    - **On** (``P_c >= m``): the hour's switch prices from :func:`determine_on_case`
      decide between

      - on-a (shutdown not allowed): increase **I** at ``p_mU``, decrease **D**
        (``P_c - m``, may clear partially) at ``p_mU - 0.01``;
      - on-b (min load never optimal): **I** at ``p_0U``, exit **X** of the whole
        position (all-or-nothing, no D) at ``p_0U - 0.01``;
      - on-c: **I** at ``p_mU``, **D** at ``p_mU - 0.01`` and **X** (``m``,
        all-or-nothing, linked to D) at ``p_0m - 0.01``.

    Every buy is priced below every sell of the same unit-hour, so the clearing never
    matches the unit with itself. Ramp limits apply to the upward volumes (F, I) only.
    Running and down times are carried forward as in flexable for off hours (the
    start bids S + F are assumed to clear), and along the committed position ``P_c``
    for running hours (I, D and X are never assumed to clear).

    Args:
        *args: Variable length argument list.
        **kwargs: Arbitrary keyword arguments (``eom_foresight`` as for flexable).
    """

    def calculate_bids(
        self,
        unit: SupportsMinMax,
        market_config: MarketConfig,
        product_tuples: list[Product],
        **kwargs,
    ) -> Orderbook:
        """
        Calculates correction bids followed by the hourly switch-price bids.

        Args:
            unit (SupportsMinMax): The power plant.
            market_config (MarketConfig): The market configuration.
            product_tuples (list[Product]): The products (one per hour).
            kwargs (dict): Additional arguments.

        Returns:
            Orderbook: The bids, corrections first.
        """
        if not product_tuples:
            return []
        if market_config.product_type != "energy":
            # the switch-price logic is defined for energy positions only
            return super().calculate_bids(unit, market_config, product_tuples, **kwargs)

        market_id = market_config.market_id
        first_start = product_tuples[0][0]
        last_end = product_tuples[-1][1]

        bids = self.calculate_correction_bids(unit, market_config, product_tuples)

        min_add, max_add = unit.calculate_min_max_power(
            first_start, last_end, product_type="energy", market_id=market_id
        )
        correction = unit.calculate_position_correction(
            first_start, last_end, market_id
        )

        # restart penalty: start-up cost after the minimum down time
        restart_cost = unit.get_starting_costs(-unit.min_down_time)

        previous_power = unit.get_output_before(first_start)
        op_time = unit.get_operation_time(first_start)

        for product, lo, hi, c in zip(product_tuples, min_add, max_add, correction):
            start, end, only_hours = product
            position = unit.outputs["energy"].at[start] + c
            if abs(position) < _EPS:
                position = 0.0
            min_load = lo + position
            available_max = hi + position

            # upward range limited by ramp rates; downward (buy) volumes are not
            ramped = unit.calculate_ramp(op_time, previous_power, hi, position)
            upper = position + min(max(ramped, max(min_load - position, 0.0)), hi)

            base = {
                "start_time": start,
                "end_time": end,
                "only_hours": only_hours,
                "node": unit.node,
            }

            if position == 0:
                min_down_running = -unit.min_down_time < op_time < 0
                off_bids = []
                if available_max > _EPS and not min_down_running:
                    off_bids = self._off_bids(
                        unit, market_id, base, min_load, upper, op_time
                    )
                bids.extend(off_bids)
                # as in flexable: assume the start bids (S + F) clear, so the next
                # hour is planned on; without bids the unit stays off
                if off_bids:
                    previous_power = upper
                    op_time = max(op_time, 0) + 1
                else:
                    previous_power = 0.0
                    op_time = min(op_time, 0) - 1
            else:
                allowed = self._shutdown_allowed(unit, start, op_time)
                bids.extend(
                    self._on_bids(
                        unit, base, position, min_load, upper, restart_cost, allowed
                    )
                )
                # committed hours: follow the position, never assume I/D/X clear
                previous_power = position
                op_time = max(op_time, 0) + 1

        return self.remove_empty_bids(bids)

    def _off_bids(
        self,
        unit: SupportsMinMax,
        market_id: str,
        base: dict,
        min_load: float,
        upper: float,
        op_time: int,
    ) -> Orderbook:
        """Start bid S (min load, all-or-nothing) and linked flexible bid F."""
        start = base["start_time"]
        bids = []
        parent_id = None
        if min_load > _EPS:
            marginal_cost = unit.calculate_marginal_cost(start, min_load)
            if op_time > 0:
                price = calculate_EOM_price_if_on(
                    unit=unit,
                    market_id=market_id,
                    start=start,
                    marginal_cost_flex=marginal_cost,
                    bid_quantity_inflex=min_load,
                    foresight=self.foresight,
                )
            else:
                price = calculate_EOM_price_if_off(
                    unit=unit,
                    marginal_cost_inflex=marginal_cost,
                    bid_quantity_inflex=min_load,
                    op_time=op_time,
                )
            parent_id = f"{unit.id}_S_{start:%Y%m%dT%H}"
            bids.append(
                {
                    **base,
                    "price": price,
                    "volume": {start: min_load},
                    "bid_type": "BB",
                    "min_acceptance_ratio": 1,
                    "bid_id": parent_id,
                }
            )
        flex = upper - min_load
        if flex > _EPS:
            bids.append(
                {
                    **base,
                    "price": unit.calculate_chord_cost(start, min_load, upper),
                    "volume": {start: flex},
                    "bid_type": "LB",
                    "parent_bid_id": parent_id,
                }
            )
        return bids

    def _on_bids(
        self,
        unit: SupportsMinMax,
        base: dict,
        position: float,
        min_load: float,
        upper: float,
        restart_cost: float,
        shutdown_allowed: bool,
    ) -> Orderbook:
        """Increase (I), decrease (D) and exit (X) bids of a running unit."""
        start = base["start_time"]
        case, prices = determine_on_case(
            unit.calculate_cost(start, min_load),
            unit.calculate_cost(start, upper),
            min_load,
            upper,
            restart_cost,
            shutdown_allowed,
        )
        sell_price = prices["p_0U"] if case == "on-b" else prices["p_mU"]

        bids = []
        increase = upper - position
        if increase > _EPS:
            bids.append({**base, "price": sell_price, "volume": increase})

        decrease = position - min_load
        has_decrease = case != "on-b" and decrease > _EPS
        decrease_id = f"{unit.id}_D_{start:%Y%m%dT%H}"
        if has_decrease:
            bids.append(
                {
                    **base,
                    "price": prices["p_mU"] - 0.01,
                    "volume": {start: -decrease},
                    "bid_type": "BB",
                    "bid_id": decrease_id,
                }
            )

        if case == "on-b":
            # the whole position in one all-or-nothing buy
            bids.append(
                {
                    **base,
                    "price": prices["p_0U"] - 0.01,
                    "volume": {start: -position},
                    "bid_type": "BB",
                    "min_acceptance_ratio": 1,
                }
            )
        elif case == "on-c":
            bids.append(
                {
                    **base,
                    "price": prices["p_0m"] - 0.01,
                    "volume": {start: -min_load},
                    "bid_type": "LB" if has_decrease else "BB",
                    "min_acceptance_ratio": 1,
                    "parent_bid_id": decrease_id if has_decrease else None,
                }
            )
        return bids

    @staticmethod
    def _shutdown_allowed(unit: SupportsMinMax, start: datetime, op_time: int) -> bool:
        """
        Whether the unit may shut down in the hour starting at *start*.

        It must have run at least ``min_operating_time`` hours (*op_time*, carried along
        the committed positions), and the next ``min_down_time - 1`` hours within the
        index must hold no position, so the off-period is not shorter than
        ``min_down_time``.
        """
        if op_time < unit.min_operating_time:
            return False
        horizon = int(unit.min_down_time) - 1
        if horizon <= 0:
            return True
        first = start + unit.index.freq
        last = min(start + horizon * unit.index.freq, unit.index[-1])
        if first > last:
            return True
        upcoming = np.asarray(unit.outputs["energy"].loc[first:last], dtype=float)
        return not (np.abs(upcoming) > _EPS).any()

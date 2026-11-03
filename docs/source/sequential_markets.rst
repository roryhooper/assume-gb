.. SPDX-FileCopyrightText: ASSUME Developers
..
.. SPDX-License-Identifier: MIT

###########################
GB-style Sequential Markets
###########################

This page describes the opt-in setup for a GB-style sequence of markets: day-ahead (DA), intraday (ID),
hourly balancing energy (BAL) and imbalance settlement. It consists of

- a balancing clearing mechanism, ``balancing_pay_as_clear``,
- sequential unit types that separate the financial position from the metered output,
- forecast vintages (DA, ID, actual) for availability and demand,
- rule-based sequential bidding strategies,
- an imbalance settlement step in the units operator, and
- a runnable example, ``example_gb_01``.

Nothing changes for existing scenarios unless the config keys described below are used (see the note on the
``profit`` output column in :doc:`release_notes`).

Market timeline
===============

All times are UTC. Auctions take place on day D and every DA and ID product delivers on day D+1 (00:00-24:00, hourly).
Bids are computed when a market opens, so each market opens after the previous one has cleared.

.. list-table::
   :header-rows: 1

   * - id
     - product_type
     - mechanism
     - open -> gate
     - products
     - first delivery
   * - DA
     - energy
     - ``pay_as_clear``
     - 08:30 -> 09:00 on D
     - 24 x 1h
     - D+1 00:00
   * - ID
     - energy
     - ``pay_as_clear``
     - 21:30 -> 22:00 on D
     - 24 x 1h
     - D+1 00:00
   * - BAL
     - balancing_energy
     - ``balancing_pay_as_clear``
     - H-70min -> H-60min, hourly
     - 1 x 1h
     - H

ASSUME's ``first_delivery`` is an offset from the market opening, not an absolute time. The example therefore uses

- DA: ``first_delivery: 15h30m`` (08:30 + 15h30m = D+1 00:00),
- ID: ``first_delivery: 2h30m`` (21:30 + 2h30m = D+1 00:00),
- BAL: ``first_delivery: 1h10m`` (opening at H-70min + 1h10m = H).

The BAL market for D+1 00:00 opens at 22:50 on D, after the ID gate. The world starts at 00:00 on the day before the first
delivery day and runs for 7 delivery days.

Position, FPN, physical output and imbalance
============================================

Every unit carries four quantities per hour:

- **Position**: what the unit has sold, net of buy-backs: DA + ID + accepted BAL volume.
- **FPN** (final physical notification): the output the unit notifies at the BAL gate, based on its actual availability or
  actual demand.
- **Physical**: FPN + accepted BAL volume, i.e. what is metered.
- **Imbalance**: physical - position = FPN - (DA + ID). An accepted BAL action moves physical output and position by the
  same amount, so it never creates or removes imbalance.

A unit's leftover volume (FPN - DA - ID) is physical and is never offered in BAL. It enters the NIV automatically and is
settled at the BAL price. Only controllable flexibility is offered in BAL: gas headroom and turn-down, battery
charge and discharge headroom, and wind and solar curtailment.

**Example (wind, 100 MW, one hour).** The DA forecast is 80 MW, so the unit sells 80. The ID forecast is 70 MW, so it buys
back 10 and its position is 70. The actual availability is 75 MW, so FPN = 75. It is not called in BAL, so its imbalance is
+5 MW, which is paid at the BAL price.

NIV and the BAL clearing rule
=============================

``balancing_pay_as_clear`` (:class:`assume.markets.clearing_algorithms.balancing.BalancingPayAsClearRole`) needs
``additional_fields: [fpn, position]``. Every unit sends at least one order per hour. A demand unit sends a zero-volume
notification order that only carries its FPN and position.

- The net imbalance volume is ``NIV = sum over units (fpn - position)``.
- ``NIV < 0`` (system short): the TSO buys ``|NIV|`` against the inc offers, in ascending price order.
- ``NIV > 0`` (system long): the TSO sells ``NIV`` against the dec bids, in descending price order.
- The TSO is the only counterparty. There is no TSO agent; its order is built from the NIV. Crossed inc/dec pairs are never
  matched, so the accepted volume equals ``|NIV|`` in one direction.
- The price is the marginal accepted price on that side of the stack.
- If ``NIV`` is approximately 0, the price is the midpoint of the best inc and best dec price.
- If there are not enough offers, the price is the maximum (short) or minimum (long) bid price and the unmet volume is logged.
- The market meta contains ``niv`` (signed), ``unmet_volume``, ``up_volume`` and ``down_volume`` (non-negative).

Sign convention. Generation and discharge are positive volumes, demand and charge are negative. Inc offers have a positive
volume, dec bids a negative volume. Imbalance is physical minus position, and ``imbalance_cashflow = imbalance * P_bal``: a
unit that is long (positive imbalance) is paid the BAL price, a unit that is short pays it. Units whose BAL offers are accepted
are not penalised for the accepted volume.

Configuration
=============

All keys are used in the study case of ``config.yaml``; see ``examples/inputs/example_gb_01/config.yaml``.

``unit_types``
  Maps the loader's unit categories to the registered unit types. The default is unchanged (``power_plant``, ``storage``,
  ``demand``).

  .. code-block:: yaml

    unit_types:
      power_plant: sequential_power_plant
      storage: sequential_storage
      demand: sequential_demand

  The sequential units (``assume/units/sequential_units.py``) never clip ``energy`` or ``balancing_energy``, which are
  financial. They write the metered output to ``fpn`` and ``physical``, return ``physical`` from
  ``execute_current_dispatch``, move the storage SOC on ``physical`` and compute generation costs from ``physical``. They also
  write ``cashflow_<market_id>``, so DA and ID revenue can be told apart (``energy_cashflow`` is still their sum).

``forecast_vintage`` (market ``param_dict``)
  Selects the forecast vintage a market uses: ``da``, ``id`` or ``actual``. The example uses DA = ``da``, ID = ``id`` and
  BAL = ``actual``. If a vintage file is missing the actual series is used.

``imbalance_settlement`` (BAL market ``param_dict``)
  ``true`` enables settlement in ``UnitsOperator.handle_market_feedback`` on BAL feedback. For each unit and hour it adds the
  accepted BAL volume to ``balancing_energy``, sets ``physical = fpn + accepted``, computes the imbalance and its cashflow at
  the BAL price, sets ``profit`` = DA + ID + BAL + imbalance cashflows - generation costs, and calls the strategy hook
  ``on_settlement``. In this mode ``unit_dispatch`` is written only on BAL feedback.

``additional_fields`` (BAL market)
  ``fpn`` and ``position``, the extra order fields the clearing needs.

Vintage files
  Loaded from the scenario folder next to the actual series (``availability_df.csv`` and ``demand_df.csv``, which stay the
  actual series): ``availability_da_df.csv``, ``availability_id_df.csv``, ``demand_da_df.csv`` and ``demand_id_df.csv``. They
  are optional. The forecasters expose them through ``get_availability(vintage)`` and ``get_demand(vintage)``.

Price forecast
  ``forecasts_df.csv`` holds a single ``price_DA`` column, computed by the generator from a merit order against the DA-vintage
  demand and renewables. Only the battery's DA bidding uses it.

Strategies
==========

All strategies derive from ``SequentialMarketStrategy`` (``assume/strategies/sequential_strategies.py``) and the same
strategy name is used in every market of a unit. ``calculate_bids`` routes on the product type to the energy bidding (DA and
ID) or the balancing bidding (BAL) and loops over every product. Energy bids are incremental around the current position P
(``energy + balancing_energy``): sell up to ``target - P``, offer to buy back ``P - floor``. P is 0 in DA, the DA volume in
ID and DA + ID in BAL. For storage, P is positive for net discharge and negative for charge. The price-taker buy price is
``maximum_bid_price`` of the market.

Realised DA prices are recorded from the strategy's own DA orders in ``calculate_reward`` (every DA order is returned with
``accepted_price`` equal to the clearing price, even if it is rejected).

.. list-table::
   :header-rows: 1

   * - unit / strategy
     - DA
     - ID
     - BAL
     - FPN
   * - gas, ``powerplant_sequential_heuristic``
     - offer ``max - P`` at mc
     - sell ``max - P`` at mc; buy back ``P - min`` at mc
     - inc ``max - P`` at mc + ``bal_markup``; dec ``P - min`` at mc - ``bal_markup``
     - position clipped to [min, max]
   * - wind / solar, ``renewable_sequential_heuristic``
     - sell ``avail_da*max - P`` at mc (about 0)
     - trade to ``avail_id*max``: sell surplus at mc, buy shortfall at the price cap
     - dec (curtail) up to the FPN at mc - ``bal_markup``
     - ``avail_actual*max``
   * - battery, ``storage_sequential_heuristic``
     - charge at <= v*eta, discharge at >= v/eta, within SOC limits, incremental to P
     - same rule, incremental to P, with v from realised DA prices
     - inc remaining discharge headroom at v/eta + ``bal_markup``; dec remaining charge headroom at v*eta - ``bal_markup``
     - position clipped to the SOC limits
   * - demand, ``demand_sequential_fixed``
     - buy ``demand_da - bought`` at fixed ``price``
     - buy shortfall vs ``demand_id`` at ``price``; sell surplus at ``sellback_price``
     - notification only (zero-volume order carrying the FPN)
     - actual demand

Rationale:

- Gas bids its marginal cost (mc), the default for a price-taker under pay-as-clear. Buying back in ID is a make-or-buy
  decision. Turning down in BAL means buying energy back from the TSO, which pays whenever the BAL price is below the fuel saved.
- Renewables always trade back to the latest forecast, since a unit cannot predict the imbalance price better than ID.
- Demand sells surplus back at ``sellback_price`` (default 0); a surplus left open earns the BAL price anyway.
- The example gas plant has ``min_power`` 0 and no ramp limits, so that a partially accepted hourly product cannot leave it
  below its minimum stable output.

Parameters
----------

Set in ``bidding_strategy_params`` of the study case:

.. list-table::
   :header-rows: 1

   * - parameter
     - default
     - effect
   * - ``bal_markup``
     - 0
     - Added to inc offer prices and subtracted from dec bid prices in BAL. It widens the offer stack around the unit's reference price and therefore moves the marginal accepted price, i.e. the imbalance price: a higher markup raises the price when the system is short and lowers it when the system is long.
   * - ``sellback_price``
     - 0
     - Price at which demand sells back surplus in ID.
   * - ``storage_foresight``
     - ``"12h"``
     - Window around each hour over which the battery averages prices to get its energy value v.

Battery behaviour
-----------------

The value of stored energy v is the mean reference price over ``+-storage_foresight``: the ``price_DA`` forecast in DA, realised
DA prices in ID and BAL. The battery records realised DA prices only from its own DA orders and falls back to the
``price_DA`` forecast where it has none. In hours where v <= 0 the charge and discharge bands would invert, so the battery
places no bids and no BAL offers in that hour.

Known limitations
=================

- The battery sizes its DA bids assuming that each is accepted. If a charge/discharge pair is only partly accepted, its
  position can become SOC-infeasible. Its FPN is then clipped to the SOC limits and the difference is carried as imbalance
  (about 6 hours in the example's base run). There is no ID repair step yet.
- ASSUME has no end-of-run dispatch flush, so the last 1-2 hours may be missing from ``unit_dispatch``.
- The following are out of scope: capacity/reserve markets, perfect-foresight and learning strategies (only the
  ``on_settlement`` hook exists), continuous ID, networks and several units per technology.

Running the example
===================

The inputs are generated by ``examples/gb/generate_example_gb_01.py`` from one week of 2019 shapes of ``example_01c``
(1000 MW peak demand, 500 MW wind, 300 MW solar, 1000 MW gas, 100 MW / 200 MWh battery). The DA forecast is the actual series
times ``1 + eps_da`` with AR(1) error ``eps_da`` (``--sigma-da``, ``--rho``), the ID forecast is the actual times
``1 + lam * eps_da + N(0, sigma-id)`` (``--lam``, ``--sigma-id``). The seed is fixed (``--seed``).

.. code-block:: bash

    # run via the examples entry point (key gb_sequential in examples/examples.py)
    python examples/examples.py   # with example = "gb_sequential"

    # or via the CLI
    assume -s example_gb_01 -c base -db "sqlite:///./examples/local_db/assume_db.db"

    # regenerate the inputs, e.g. a perfect-forecast check
    python examples/gb/generate_example_gb_01.py --sigma-da 0 --sigma-id 0

Things to expect:

- With ``--sigma-da 0 --sigma-id 0`` (perfect forecasts) NIV is 0 in every hour and no unit has an imbalance. This is checked by
  ``tests/test_sequential_integration.py``.
- The warnings ``Forecast could not be calculated for BAL`` at startup are expected: BAL is not an energy product.
- The DA price spikes in the base run (up to about 863 EUR/MWh on 2019-01-10) are expected. Gas capacity equals peak demand,
  so a DA over-forecast of demand at low wind pushes the merit-order ``price_DA`` forecast to the 3000 cap in a few hours,
  and the battery's valuation reflects it.

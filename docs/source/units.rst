.. SPDX-FileCopyrightText: ASSUME Developers
..
.. SPDX-License-Identifier: MIT

###########
Unit Types
###########

In power system modeling, various unit types are used to represent the components responsible for generating, storing, and consuming electricity. These units are essential for simulating the operation of the grid and ensuring a reliable balance between supply and demand.

The primary unit types in this context include:

1. **Power Plants**: These are conventional or renewable energy generators, such as coal, gas, wind, or solar power plants. They are responsible for supplying electricity to the grid based on the system's demand.

2. **Storage Units**: These units, like batteries or pumped hydro storage, can store electricity when supply exceeds demand and release it when needed, adding flexibility to the grid.

3. **Demand Units**: These represent consumers of electricity, such as households, industries, or commercial buildings, whose electricity consumption is typically fixed and not easily adjustable based on real-time grid conditions. Demand units will therefore be modelled with inelastic demand most often. However, representation of elastic bidding is possible with this unit type.

Each unit type has specific characteristics that affect how the power system operates, and understanding these is key to modeling and optimizing grid performance.


Units in sequential markets
===========================

When a unit trades the same delivery hours on several markets one after another (see :ref:`sequential-markets`), the later markets see what the unit has already sold or bought. This section explains how units account for that.

Position and physical output
----------------------------

Before delivery, the output ``energy`` of a unit holds its *position*: the net volume accepted over all markets so far. After delivery it holds the *physical output*. The outputs of units and the strategies' bids in a later market are measured relative to the position, so a unit can sell more, or buy back (reduce) its position. The difference between what was delivered and the position is exported as ``energy_deviation`` (physical output minus position) in ``unit_dispatch``. A deviation can occur if a correction or a reduction did not clear or if operating constraints, such as minimum operating and down times, force a different output than the traded position. Ramp limits and min load clamps can also cause deviations, so they occur in single-market simulations too. At the end of a run the ``UnitsOperator`` logs one summary (level ``INFO``) per unit whose delivery deviated from its position; the per-window details are logged at level ``DEBUG``.

Per-market outputs
------------------

For every market ``<m>`` a unit trades on, the following outputs are recorded and exported in ``unit_dispatch``:

====================================  ====================================================================================
Output                                Meaning
====================================  ====================================================================================
``energy_<m>``                        Volume accepted on market ``<m>``; the sum over markets is the position.
``energy_cashflow_<m>``               Cashflow of market ``<m>``; the sum over markets is ``energy_cashflow``.
``energy_total_costs_<m>``            Change in operating costs (marginal and start-up costs) caused by the volume of ``<m>``.
``energy_profit_<m>``                 ``energy_cashflow_<m>`` minus ``energy_total_costs_<m>``.
``energy_deviation``                  Physical output minus position.
====================================  ====================================================================================

The costs are attributed sequentially: each market is charged with the change in cost of the whole position that its volume causes, for example the start-up cost a unit avoids by buying back a position on a later market is credited to that market. The per-market profits therefore add up to the total cashflow minus the cost of the final position. Demand units have no operating cost; their profit equals their payments.

Forced corrections
------------------

If a market's forecast makes an earlier position impossible to deliver (e.g. a day-ahead sale of wind power that the intraday forecast no longer expects, or a thermal position between zero and min load), the unit must correct it. Strategies that support this prepend *correction bids* to their orderbook (``BaseStrategy.calculate_correction_bids``): price-taker bids for the volume to correct, which may clear partly. A unit that must reduce its output buys at ``maximum_bid_price``, a unit that must increase sells at ``minimum_bid_price``. The price is set in a single method, ``BaseStrategy.correction_price``, which strategies can override, for instance to price the correction at an expected imbalance price. ``DemandEnergyNaiveStrategy`` (``demand_energy_naive``) overrides it to buy at the demand's willingness to pay (its ``price``). Corrections exist for energy products only. If no counterparty exists for a correction, the remainder stays as a deviation. Units with an earlier position that do not need a correction bid only economically: the naive strategy ``powerplant_energy_naive`` additionally buys back part of a position at marginal cost when the unit can reduce it, and the thermal strategy ``powerplant_energy_heuristic_flexable_hourly_linked`` uses switch prices, see :doc:`bidding_strategies`.

Partial-load efficiency
-----------------------

Power plants lose efficiency when they run below full load. This is modelled if the unit sets ``partial_load_eff`` to true: the marginal cost then depends on the power level, and the efficiency-loss curve is selected by ``technology`` (``combined cycle gas turbine``, ``open cycle gas turbine``, ``hard coal`` or ``lignite``) and, if the technology is not listed, by ``fuel_type``. Without ``partial_load_eff`` the marginal cost is constant at full load efficiency (previously ``partial_load_eff`` had no effect). Strategies that bid below full load use the average cost at the chosen power level. Naive price forecasts use a full-load cost for these units.

.. note::
   The start-cost thresholds ``downtime_hot_start`` and ``downtime_warm_start`` default to 0 hours, which makes every start a cold start. Set them explicitly (e.g. 8 and 48 hours) if the three start costs should be used. ``example_01k`` does so.

.. include:: demand_side_agent.rst

.. SPDX-FileCopyrightText: ASSUME Developers
..
.. SPDX-License-Identifier: MIT

Bidding Strategies
==================

Bidding strategies are a core concept of ASSUME which describe how agents bid their assets on a market.

Overview
---------

As described in the :ref:`exchangeable_bidding_strategy`, a Bidding Strategy dictates the bidding behavior of units in different markets, whereby it maps certain states and technical constraints to bidding decisions.
In general, there is a distinction between two kinds of strategy classes:

- :py:meth:`assume.strategies.portfolio_strategies.UnitOperatorStrategy`, which indicate the use in a UnitsOperator and can be used to provide a Portfolio optimization.
- Strategies used for a single unit of :doc:`units`, which range from naive (e.g. :py:meth:'assume.strategies.naive_strategies.EnergyNaiveStrategy')to advanced (e.g. :py:meth:'assume.strategies.advanced_orders.EnergyHeuristicFlexableLinkedStrategy').

Both types have a function `calculate_bids` which is called with the information of the market and bids to bid on.

UnitOperatorStrategy
--------------------

The UnitsOperatorStrategies can be used to adjust the behavior of the UnitsOperator.
The default is the :py:meth:`assume.strategies.portfolio_strategies.UnitsOperatorDirectStrategy`.
It formulates the bids to the market according to the bidding strategy of the each unit individually.
This calls `calculate_bids`` of each unit and returns the aggregated list of all individual bids of all units.
This is the default for all UnitsOperators which do not have a separate strategy configured.

Another implementation includes the :py:meth:`assume.strategies.portfolio_strategies.UnitsOperatorEnergyHeuristicCournotStrategy` that adds a markup to the marginal cost of each unit of the units operator.
The marginal cost is computed with EnergyNaiveStrategy and the markup depends on the total capacity of the unit operator.

UnitStrategies
--------------

The ASSUME framework provides multiple options in terms of Bidding Strategy methodologies:

==============================  =============================================================
Bidding Strategy Methodology    Description
==============================  =============================================================
Naive                           Simple bidding strategies for participating in a market mechanism that follows the Merit Order principle. No additional dependencies needed.
Heuristic                       Basic methodology to form bids, based on participating in a market mechanism that follows the Merit Order principle. These strategies do not utilise
                                forecasting or consider finer details such as the effects of changing a power plant's operational state (start-up costs etc.), so the bid volume is
                                of the order of its maximum power capacity (given ramping constraints), and the bid price is set to the marginal cost.
Optimization                    Methodology, based on the flexABLE methodology [`Qussous et al. 2022 <https://doi.org/10.3390/en15020494>`_], offers more refined strategising
                                compared to the Naive methods. Applicable to power plant and storage units, it incorporates market dynamics to a greater degree by forecasting market
                                prices, as well as accounting for operational history, potential power loss due to heat production, and the fact that a plant can make bids for
                                multiple markets at the same time.
Learning                        A :doc:`reinforcement learning <learning>` (RL) approach to formulating bids for an Energy-Only Market.
                                Agents perform actions (choose bid price(s) and for storage a direction) informed by observations (including forecasted residual load, forecasted
                                price, marginal cost). Bid volumes are fixed to the maximum possible volume. Based on the reward (profits) from accepted bids, agents learn to optimise
                                bids to maximise profits. This requires to have PyTorch installed.
Interactive                     Strategies which let a user handle input through a terminal or other interface.
==============================  =============================================================


For each Bidding Strategy methodology there are multiple Bidding Strategy options depending on the product and market that the bid is intended for,
as well as the type of unit making the bid.

Accordingly, each Bidding Strategy has an associated ID which takes the form "unit_product_method_comment", the comment being optional.
This "bidding_strategy_id" needs to be entered when defining a unit's bidding strategy. Each Bidding Strategy and associated ID for each methodology is defined and described further below.

When constructing a units CSV file, the bidding strategies are set using :code:`"bidding_*"` columns, where the market type the bidding strategy is applied to
follows the underscore (the market names need to match with those in the config file).

======================  ===========  ==================================  ============================================  ============================================  ===========
name                    technology   bidding_EOM                         bidding_CRM_pos                               bidding_CRM_neg                               max_power
======================  ===========  ==================================  ============================================  ============================================  ===========
Naive-Bidding Unit      hydro        powerplant_energy_naive             powerplant_capacity_heuristic_balancing_pos   powerplant_capacity_heuristic_balancing_neg   1000
Advanced-Bidding Unit   hydro        powerplant_energy_heuristic_block   powerplant_capacity_heuristic_balancing_pos   powerplant_capacity_heuristic_balancing_neg   1000
======================  ===========  ==================================  ============================================  ============================================  ===========

We'll now take a look at the different Bidding Strategies within each methodology, their associated "bidding_strategy_id", and for which unit and market type(s) they are valid.

Naive
-----

==================================  =======================  ==================================================================================================================================
bidding_strategy_id                 For Market Types         Description
==================================  =======================  ==================================================================================================================================
powerplant_energy_naive             EOM, CRM_pos, CRM_neg    Basic strategy for merit order markets at a single timepoint (hour). Uses marginal cost as bid price and the maximum feasible
                                                             power (respecting ramping constraints) as bid volume.
demand_energy_naive                 EOM, CRM_pos, CRM_neg    Basic naive strategy for demand units. Can realise a price-inelastic demand by setting a very high bid price and volume equal
                                                             to demand at the timepoint.
powerplant_energy_naive_otc         OTC                      Similar to powerplant_energy_naive but for OTC (bilateral) trades.
demand_energy_naive_otc             OTC                      Similar to demand_energy_naive but for OTC (bilateral) trades.
powerplant_energy_naive_profile     EOM, CRM_pos, CRM_neg    Similar to powerplant_energy_naive but submitted as a 24-hour block (Day-Ahead). Bid price is set to the marginal cost at the
                                                             starting timepoint.
powerplant_energy_naive_redispatch  redispatch               Submits unit info and currently dispatched power for upcoming hours to the redispatch market (includes marginal cost, ramping,
                                                             and dispatch information).
demand_energy_naive_redispatch      redispatch               Submits unit info and currently dispatched power for upcoming hours to the redispatch market (includes marginal cost, ramping,
                                                             and dispatch information).
household_energy_naive_redispatch   redispatch               Naive DSM strategy for industry/household units; bids available flexibility on redispatch. Volume equals flexible power at product
                                                             start; price equals marginal cost at product start.
industry_energy_naive_redispatch    redispatch               Naive DSM strategy for industry/household units; bids available flexibility on redispatch. Volume equals flexible power at product
                                                             start; price equals marginal cost at product start.
exchange_energy_naive               EOM                      Incorporates cross-border trading: submits export (negative volume) and import bids. Exports are treated as demand with very high
                                                             bid price; imports as supply with a very low bid price to virtually guarantee acceptance.
==================================  =======================  ==================================================================================================================================

Naive method API references:

- :py:meth:`assume.strategies.naive_strategies.EnergyNaiveStrategy`
- :py:meth:`assume.strategies.extended.EnergyNaiveOtcStrategy`
- :py:meth:`assume.strategies.naive_strategies.EnergyNaiveProfileStrategy`
- :py:meth:`assume.strategies.naive_strategies.EnergyNaiveRedispatchStrategy`
- :py:meth:`assume.strategies.naive_strategies.DsmEnergyNaiveRedispatchStrategy`
- :py:meth:`assume.strategies.naive_strategies.ExchangeEnergyNaiveStrategy`

Heuristic
---------

============================================  ==================  ==================================================================================================================
bidding_strategy_id                           For Market Types    Description
============================================  ==================  ==================================================================================================================
demand_energy_heuristic_elastic               EOM                 This bidding strategy is formulated for a demand unit to realise a "price-elastic" demand bid, approximating a
                                                                  marginal utility curve. The elasticity can be set to "linear" or "isoelastic".
powerplant_energy_heuristic_flexable          EOM                 A more refined approach to bidding on the EOM compared to naive. A unit submits both inflexible and flexible
                                                                  bids per hour. The inflexible bid represents the minimum power output, priced at marginal cost plus startup
                                                                  costs, while the flexible bid covers additional power up to the maximum capacity at marginal cost. It
                                                                  incorporates price forecasting and accounts for ramping constraints, operational history, and power loss
                                                                  due to heat production.
powerplant_energy_heuristic_block             EOM                 A power plant strategy valid for complex market clearing which bids dict blocks to the market. The bid is for a
                                                                  block of multiple hours instead of being for a single hour. A minimum acceptance ratio (MAR) defines how to
                                                                  handle rejected bids within individual hours of the block. For the inflexible bid, the MAR is set to 1,
                                                                  meaning all bids within the block must be accepted otherwise the whole block bid is rejected. A separate MAR
                                                                  can be set for children (flexible) bids. See the Advanced Orders tutorial:
                                                                  https://assume.readthedocs.io/en/latest/examples/06_advanced_orders_example.html#1.-Basics
powerplant_energy_heuristic_linked            EOM                 A power plant strategy which handles block and linked bids on a market with these fields as a dict. The strategy
                                                                  is similar to :code:`powerplant_energy_heuristic_block` but allows to integrate the flexible bids as linked bids.
                                                                  See the Advanced Orders tutorial:
                                                                  https://assume.readthedocs.io/en/latest/examples/06_advanced_orders_example.html#1.-Basics
powerplant_capacity_heuristic_balancing_neg   CRM_neg             A bid on the negative Capacity or Energy Control Reserve Market (CRM); volume is determined by calculating how
                                                                  much it can reduce power. The capacity price is found by comparing the revenue it could receive if it bid this
                                                                  volume on the EOM; the energy price is the negative of marginal cost.
powerplant_capacity_heuristic_balancing_pos   CRM_pos             A bid on the positive Capacity or Energy CRM; volume is determined by calculating how much it can increase
                                                                  power. The capacity price is found by comparing the revenue it could receive if it bid this volume on
                                                                  the EOM; the energy price is the positive of marginal cost.
storage_energy_heuristic_flexable             EOM                 Determines strategy of a Storage unit bidding on the EOM. The unit acts as a generator or load based on the
                                                                  average price forecast. If the current price forecast is greater than the average price, the Storage unit
                                                                  will bid to discharge at a price equal to the average price divided by the discharge efficiency. Otherwise,
                                                                  it will bid to charge at the average price multiplied by the charge efficiency. Calculates ramping
                                                                  constraints for charging and discharging based on theoretical state of charge (SOC), ensuring that power
                                                                  output is feasible. The bid volume is subject to the charge/discharge capacity of the unit.
storage_capacity_heuristic_balancing_neg      CRM_neg             Analogous to :code:`storage_energy_heuristic_flexable`, but bids either on the negative capacity CRM or
                                                                  energy CRM.
storage_capacity_heuristic_balancing_pos      CRM_pos             Analogous to :code:`storage_energy_heuristic_flexable`, but bids either on the positive capacity CRM or
                                                                  energy CRM.
============================================  ==================  ==================================================================================================================

Heuristic method API references:

- :py:meth:`assume.strategies.naive_strategies.EnergyHeuristicElasticStrategy`

- :py:meth:`assume.strategies.advanced_orders.EnergyHeuristicFlexableBlockStrategy`
- :py:meth:`assume.strategies.advanced_orders.EnergyHeuristicFlexableLinkedStrategy`
- :py:meth:`assume.strategies.flexable.CapacityHeuristicBalancingNegStrategy`
- :py:meth:`assume.strategies.flexable.CapacityHeuristicBalancingPosStrategy`

- :py:meth:`assume.strategies.flexable_storage.StorageEnergyHeuristicFlexableStrategy`
- :py:meth:`assume.strategies.flexable_storage.StorageCapacityHeuristicBalancingNegStrategy`
- :py:meth:`assume.strategies.flexable_storage.StorageCapacityHeuristicBalancingPosStrategy`

Switch-price strategy for sequential markets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``powerplant_energy_heuristic_flexable_hourly_linked`` is a thermal power plant strategy for several energy markets that trade the same delivery hours one after another (see :ref:`sequential-markets`), cleared with ``complex_clearing`` (``pricing_mechanism: pay_as_clear``). It is implemented in :py:meth:`assume.strategies.advanced_orders.EnergyHeuristicFlexableHourlyLinkedStrategy` and reuses the start-up markup, stay-on discount, ramping and operating-history logic of ``powerplant_energy_heuristic_flexable``. In each market and for each delivery hour it first submits *correction bids* if the position from an earlier market cannot be delivered (see :doc:`units`), then:

- **Off unit:** an all-or-nothing single-hour start block *S* at the min-load volume, priced at the average cost at min load plus the start-up markup (the start-up cost uses the full down time before the hour, as in the accounting of the unit), and a linked flexible bid *F* for the volume up to the available maximum, priced at the average cost of that range. The market judges *S* together with its linked *F*, so they act like one "start and run" offer. A unit still inside its minimum down time bids nothing.
- **Running unit:** a unit that already holds a position decides for the hour between its extremes: shutting down (if allowed), min load and max load. Its profit at each extreme is a straight line in the market price, so there are *switch prices* at which the best extreme changes. The unit offers an increase *I* (sell up to the maximum) at the switch price towards max load, a decrease *D* (buy back down to min load) 0.01 EUR/MWh below it, and, if shutting down is allowed, an exit *X* (buy back the min-load volume, or the whole position) at the switch price towards zero output. Shutting down is allowed only if the minimum operating time has been served and shutting down does not create an off period shorter than the minimum down time.

Using a single switch price per hour (instead of the average cost of the reduction and of the increase separately) keeps every buy price below every sell price of the same unit. Otherwise a unit with partial-load efficiency could be matched with itself and end up between zero and min load. Bids that clear fully are always profitable for the unit.

Limitations:

- **Hour-at-a-time decisions:** effects across hours enter only through the restart penalty and the shutdown-allowed check. A unit committed for a long run in the first market can only shut down in a later market at the end of that run, although it can always reduce to min load. A run no longer than ``min_operating_time`` can never be exited in a later market, only reduced to min load. Minimum up/down times are enforced at delivery, which can lead to recorded deviations between position and physical output (``energy_deviation``).
- **Partial acceptance at the switch price:** a bid that clears only partly at exactly its switch price can lose a little, bounded by the difference between incremental cost and switch price over that volume.
- **ID price forecast:** the naive price forecast of a later market ignores the effect of earlier commitments (start-ups already paid for, storage state of charge).
- **Removed start blocks are not re-admitted:** when a start block and its linked flexible bid clear with the flexible bid setting the price at its average cost, their joint surplus is negative and ``complex_clearing`` removes the block and re-solves. The removal is permanent, so with idle thermal capacity demand can be left unserved at the price cap. In ``example_01k`` this is the dominant cause of cap prices and unserved demand, see its README.

Optimization
------------

===========================================  ==================  ============
bidding_strategy_id                          For Market Types    Description
===========================================  ==================  ============
household_energy_optimization                EOM                 An energy strategy of a Household DSM unit. The bid volume is the optimal power requirement of the optimization.
industry_energy_optimization                 EOM                 An energy strategy of a Industry DSM unit. The bid volume is the optimal power requirement of the optimization.
household_capacity_heuristic_balancing_neg   CRM_neg             A negative capacity strategy of a Household DSM unit. The bid volume is the optimal power requirement of the optimization.
household_capacity_heuristic_balancing_pos   CRM_pos             A positive capacity strategy of a Industry DSM unit. The bid volume is the optimal power requirement of the optimization.
industry_capacity_heuristic_balancing_neg    CRM_neg             A negative capacity strategy of a Household DSM unit. The bid volume is the optimal power requirement of the optimization.
industry_capacity_heuristic_balancing_pos    CRM_pos             A positive capacity strategy of a Industry DSM unit. The bid volume is the optimal power requirement of the optimization.
powerplant_energy_optimization_dmas          EOM                 Power plant strategy using forecast optimization and avoided cost calculation used for smart bids coming from the DMAS methodology
storage_energy_optimization_dmas             EOM                 Storage strategy using forecasts and avoided cost calculation used for smart bids coming from the DMAS methodology
===========================================  ==================  ============

Optimization method API references:

- :py:meth:`assume.strategies.naive_strategies.DsmEnergyOptimizationStrategy`
- :py:meth:`assume.strategies.naive_strategies.DsmCapacityHeuristicBalancingStrategy`
- :py:meth:`assume.strategies.dmas_powerplant.EnergyOptimizationDmasStrategy`
- :py:meth:`assume.strategies.dmas_storage.StorageEnergyOptimizationDmasStrategy`

Learning
--------

===================================== ================== =============================================================
bidding_strategy_id                   For Market Types   Description
===================================== ================== =============================================================
powerplant_energy_learning            EOM                A :ref:`reinforcement learning <td3learning>` (RL) approach to formulating bids for a
                                                         Power Plant in an Energy-Only Market. The agent's actions are
                                                         two bid prices: one for the inflexible component (P_min) and another for the flexible component (P_max - P_min) of a unit's capacity.
                                                         The bids are informed by 50 observations, which include forecasted residual load, forecasted price, total capacity, and marginal cost,
                                                         all contributing to decision-making. Noise is added to the action, especially towards the beginning of the learning, to encourage exploration and novelty.
                                                         The reward is calculated based on profits from executed bids, operational costs, opportunity costs (penalizing underutilized capacity),
                                                         and a regret term to minimize missed revenue opportunities. This approach encourages full utilization of the unit's capacity.
storage_energy_learning               EOM                Similar RL approach as :code:`learning_eom_powerplant`, for a Storage unit. The make-up of the observations is similar to those for
                                                         :code:`learning_eom_powerplant`, with an additional observation being the State-of-Charge (SOC) of the storage unit. The agent has 2 actions -
                                                         a bid price, and a bid direction (to buy, sell or do nothing). The bid volume is subject to the charge/discharge capacity of the unit.
                                                         The reward is calculated based on profits from executed bids, with fixed costs for charging/discharging incorporated.
powerplant_energy_learning_single_bid EOM                Reinforcement Learning Strategy with Single-Bid Structure for Energy-Only Markets.
                                                         This strategy is a simplified variant of the standard `EnergyLearningStrategy`, which typically submits two
                                                         separate price bids for inflexible (P_min) and flexible (P_max - P_min) components. Instead,
                                                         `EnergyLearningSingleBidStrategy` submits a single bid that always offers the unit's maximum power,
                                                         effectively treating the full capacity as inflexible from a bidding perspective.
renewable_energy_learning_single_bid  EOM                Reinforcement Learning Strategy for a renewable unit that enables the agent to learn
                                                         optimal bidding strategies on an Energy-Only Market.
===================================== ================== =============================================================

Learning method API references:

- :py:meth:`assume.strategies.learning_strategies.EnergyLearningStrategy`
- :py:meth:`assume.strategies.learning_strategies.EnergyLearningSingleBidStrategy`
- :py:meth:`assume.strategies.learning_strategies.StorageEnergyLearningStrategy`
- :py:meth:`assume.strategies.learning_strategies.RenewableEnergyLearningSingleBidStrategy`

Other
-----

=============================== ================ ================== =============================================================
bidding_strategy_id             For Unit Types   For Market Types   Description
=============================== ================ ================== =============================================================
powerplant_energy_interactive   Any              Any                The bidding volume and price is manually entered in the terminal.
=============================== ================ ================== =============================================================

Miscellaneous method API references:

- :py:meth:`assume.strategies.interactive_strategies.EnergyInteractiveStrategy`

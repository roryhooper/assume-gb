# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import pytest

from assume.strategies.advanced_orders import determine_on_case

# CCGT reference (spec section 5.6/6.4): fuel 25, co2 25, emission factor 0.2,
# efficiency 0.55, max 100 MW, min 40 MW
CCGT_LOSS_40 = (
    0.178749 * 0.4**4
    - 0.653192 * 0.4**3
    + 0.964704 * 0.4**2
    - 0.805845 * 0.4
    + 0.315584
)
COST_M = 40 * 30 / (0.55 - CCGT_LOSS_40)  # ~2729.6
COST_U = 100 * 30 / 0.55  # ~5454.5


# =============================================================================
# determine_on_case
# =============================================================================


def test_switch_prices_ccgt_r0_is_on_b():
    case, prices = determine_on_case(COST_M, COST_U, 40, 100, 0, True)
    assert case == "on-b"
    assert prices["p_mU"] == pytest.approx(45.4, abs=0.05)
    assert prices["p_0U"] == pytest.approx(54.5, abs=0.05)
    assert prices["p_0m"] == pytest.approx(68.2, abs=0.05)
    assert prices["p_mU"] == pytest.approx((COST_U - COST_M) / 60)
    assert prices["p_0U"] == pytest.approx(COST_U / 100)
    assert prices["p_0m"] == pytest.approx(COST_M / 40)


def test_switch_prices_ccgt_hot_start_is_on_c():
    case, prices = determine_on_case(COST_M, COST_U, 40, 100, 2265, True)
    assert case == "on-c"
    assert prices["p_mU"] == pytest.approx(45.4, abs=0.05)
    assert prices["p_0U"] == pytest.approx((COST_U - 2265) / 100)
    assert prices["p_0m"] == pytest.approx((COST_M - 2265) / 40)
    assert prices["p_0m"] < prices["p_mU"]


def test_on_a_when_shutdown_not_allowed():
    for R in (0, 2265):
        case, prices = determine_on_case(COST_M, COST_U, 40, 100, R, False)
        assert case == "on-a"
        assert prices["p_mU"] == pytest.approx((COST_U - COST_M) / 60)


def test_switch_prices_degenerate_ranges():
    # U == m: no flexible range, p_mU falls back to p_0U (and on-b)
    case, prices = determine_on_case(COST_U, COST_U, 100, 100, 0, True)
    assert prices["p_mU"] == pytest.approx(prices["p_0U"])
    assert case == "on-b"
    # m == 0: running at zero always beats a restart, never a shutdown block
    case, prices = determine_on_case(0.0, COST_U, 0, 100, 10, True)
    assert prices["p_0m"] == float("-inf")
    assert case == "on-c"

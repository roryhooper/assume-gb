# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

from datetime import datetime, timedelta

import pandas as pd
import pytest

from assume.common.forecaster import PowerplantForecaster
from assume.strategies.naive_strategies import EnergyNaiveStrategy
from assume.units import PowerPlant


@pytest.fixture
def power_plant_1() -> PowerPlant:
    # Create a PowerPlant instance with some example parameters
    index = pd.date_range("2022-01-01", periods=4, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        fuel_prices={"lignite": [10, 11, 12, 13], "co2": [10, 20, 30, 30]},
        market_prices={"EOM": 0},
    )
    return PowerPlant(
        id="test_pp",
        unit_operator="test_operator",
        technology="hard coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=200,
        efficiency=0.5,
        additional_cost=10,
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
    )


@pytest.fixture
def power_plant_2() -> PowerPlant:
    # Create a PowerPlant instance with some example parameters
    index = pd.date_range("2022-01-01", periods=4, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    return PowerPlant(
        id="test_pp",
        unit_operator="test_operator",
        technology="hard coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=0,
        efficiency=0.5,
        additional_cost=10,
        fuel_type="lignite",
        forecaster=forecaster,
        emission_factor=0.5,
    )


@pytest.fixture
def power_plant_3() -> PowerPlant:
    # Create a PowerPlant instance with some example parameters
    index = pd.date_range("2022-01-01", periods=4, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    return PowerPlant(
        id="test_pp",
        unit_operator="test_operator",
        technology="hard coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=0,
        efficiency=0.5,
        additional_cost=10,
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
        partial_load_eff=True,
    )


def test_init_function(power_plant_1, power_plant_2, power_plant_3):
    assert power_plant_1.id == "test_pp"
    assert power_plant_1.unit_operator == "test_operator"
    assert power_plant_1.technology == "hard coal"
    assert power_plant_1.max_power == 1000
    assert power_plant_1.min_power == 200
    assert power_plant_1.efficiency == 0.5
    assert power_plant_1.additional_cost == 10
    assert power_plant_1.fuel_type == "lignite"
    assert power_plant_1.emission_factor == 0.5
    assert power_plant_1.ramp_up is None
    assert power_plant_1.ramp_down is None

    index = pd.date_range("2022-01-01", periods=4, freq="h")
    assert (
        power_plant_1.marginal_cost == pd.Series([40.0, 52.0, 64.0, 66.0], index)
    ).all()

    assert (power_plant_2.marginal_cost == pd.Series(40, index)).all()
    # partial-load efficiency: no precomputed series, cost depends on the load
    assert power_plant_3.marginal_cost is None


def test_reset_function(power_plant_1):
    # Expected series with zero values
    expected_series = pd.Series(
        0.0, index=pd.date_range("2022-01-01", periods=4, freq="h")
    )

    # Check if total_power_output is reset
    assert (power_plant_1.outputs["energy"].data == expected_series.values).all()

    # The same for pos and neg capacity reserve
    assert (power_plant_1.outputs["pos_capacity"].data == expected_series.values).all()
    assert (power_plant_1.outputs["neg_capacity"].data == expected_series.values).all()

    # The same for total_heat_output and power_loss_chp
    assert (power_plant_1.outputs["heat"].data == expected_series.values).all()
    assert (power_plant_1.outputs["power_loss"].data == expected_series.values).all()


def test_calculate_operational_window(power_plant_1):
    start = datetime(2022, 1, 1, 0)
    end = datetime(2022, 1, 1, 1)
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )

    min_cost = power_plant_1.calculate_marginal_cost(start, min_power[0])
    max_cost = power_plant_1.calculate_marginal_cost(start, max_power[0])

    assert min_power[0] == 200
    assert min_cost == 40.0

    assert max_power[0] == 1000
    assert max_cost == 40

    assert power_plant_1.outputs["energy"].at[start] == 0


def test_powerplant_feedback(power_plant_1, mock_market_config):
    start = datetime(2022, 1, 1, 0)
    end = datetime(2022, 1, 1, 1)
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )
    min_cost = power_plant_1.calculate_marginal_cost(start, min_power[0])
    max_cost = power_plant_1.calculate_marginal_cost(start, max_power[0])

    assert min_power[0] == 200
    assert min_cost == 40.0

    assert max_power[0] == 1000
    assert max_cost == 40
    assert power_plant_1.outputs["energy"].at[start] == 0

    orderbook = [
        {
            "start_time": start,
            "end_time": end,
            "only_hours": None,
            "price": min_cost,
            "accepted_price": min_cost,
            "accepted_volume": min_power[0],
        }
    ]

    # min_power gets accepted
    mc = mock_market_config
    power_plant_1.set_dispatch_plan(mc, orderbook)

    # second market request for same interval
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )

    # we do not need additional min_power, as our runtime requirement is fulfilled
    assert min_power[0] == 0
    # we can not bid the maximum anymore, because we already provide energy on the other market
    assert max_power[0] == 800

    # second market request for next interval
    start = datetime(2022, 1, 1, 1)
    end = datetime(2022, 1, 1, 2)
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )

    # now we can bid max_power and need min_power again
    assert min_power[0] == 200
    assert max_power[0] == 1000


def test_powerplant_ramping(power_plant_1):
    power_plant_1.ramp_down = 100
    power_plant_1.ramp_up = 200
    power_plant_1.min_operating_time = 3
    power_plant_1.min_down_time = 2
    power_plant_1.min_power = 50

    start = datetime(2022, 1, 1, 0)
    end = datetime(2022, 1, 1, 1)
    end_excl = end - power_plant_1.index.freq
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )

    assert min_power[0] == 50
    assert max_power[0] == 1000

    op_time = power_plant_1.get_operation_time(start)
    assert op_time == 3

    min_cost = power_plant_1.calculate_marginal_cost(start, min_power[0])
    max_cost = power_plant_1.calculate_marginal_cost(start, max_power[0])
    max_ramp = power_plant_1.calculate_ramp(op_time, 100, max_power[0])
    min_ramp = power_plant_1.calculate_ramp(op_time, 100, min_power[0])

    assert min_ramp == 50
    assert min_cost == 40.0

    assert max_ramp == 300
    assert max_cost == 40

    # min_power gets accepted

    power_plant_1.outputs["energy"].loc[start:end_excl] += 300

    # next hour
    start = datetime(2022, 1, 1, 1)
    end = datetime(2022, 1, 1, 2)
    end_excl = end - power_plant_1.index.freq

    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )

    assert min_power[0] == 50
    assert max_power[0] == 1000

    op_time = power_plant_1.get_operation_time(start)
    assert op_time == 1

    min_ramp = power_plant_1.calculate_ramp(op_time, 300, min_power[0])
    max_ramp = power_plant_1.calculate_ramp(op_time, 300, max_power[0])

    assert min_ramp == 200
    assert max_ramp == 500

    # accept max_power
    power_plant_1.outputs["energy"].loc[start:end_excl] += 500

    # next hour
    start = datetime(2022, 1, 1, 2)
    end = datetime(2022, 1, 1, 3)
    end_excl = end - power_plant_1.index.freq

    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )

    op_time = power_plant_1.get_operation_time(start)
    assert op_time == 2

    min_ramp = power_plant_1.calculate_ramp(op_time, 500, min_power[0])
    max_ramp = power_plant_1.calculate_ramp(op_time, 500, max_power[0])

    assert min_ramp == 400
    assert max_ramp == 700

    # ramp_up if min_down_time is not reached
    power_plant_1.outputs["energy"].loc[start - power_plant_1.index.freq] = 0

    op_time = power_plant_1.get_operation_time(start)
    assert op_time == -1

    min_ramp = power_plant_1.calculate_ramp(op_time, 0, 0)
    max_ramp = power_plant_1.calculate_ramp(op_time, 0, 100)

    assert min_ramp == 0
    assert max_ramp == 0

    # ramp_down if min_operating_time is not reached
    power_plant_1.outputs["energy"].loc[start - power_plant_1.index.freq * 2] = 0
    power_plant_1.outputs["energy"].loc[start - power_plant_1.index.freq] = 100

    op_time = power_plant_1.get_operation_time(start)
    assert op_time == 1

    min_ramp = power_plant_1.calculate_ramp(op_time, 100, 0)
    max_ramp = power_plant_1.calculate_ramp(op_time, 100, 1000)

    assert min_ramp == 50
    assert max_ramp == 300


def test_powerplant_availability(power_plant_1):
    index = pd.date_range("2022-01-01", periods=4, freq="h")
    ff = PowerplantForecaster(
        index,
        availability=[0.5, 0.01, 1, 1],
        fuel_prices={"others": [10, 11, 12, 13], "co2": [10, 20, 30, 30]},
    )
    # set availability
    power_plant_1.forecaster = ff
    power_plant_1.max_power = 1000
    power_plant_1.min_power = 200
    power_plant_1.ramp_down = 1000
    power_plant_1.ramp_up = 1000

    start = datetime(2022, 1, 1, 0)
    end = datetime(2022, 1, 1, 1)
    ### HOUR 0
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )
    op_time = power_plant_1.get_operation_time(start)
    max_ramp = power_plant_1.calculate_ramp(op_time, 0, max_power[0])
    assert max_ramp == power_plant_1.max_power / 2

    ### HOUR 1
    start += timedelta(hours=1)
    end += timedelta(hours=1)
    _, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )
    op_time = power_plant_1.get_operation_time(start)
    # do not run if 0 < power <= min_power is needed
    max_ramp = power_plant_1.calculate_ramp(op_time, 0, max_power[0])
    assert max_ramp == 0.0

    ### HOUR 2
    start += timedelta(hours=1)
    end += timedelta(hours=1)
    _, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type="energy"
    )
    op_time = power_plant_1.get_operation_time(start)
    max_ramp = power_plant_1.calculate_ramp(op_time, 0, max_power[0])
    assert max_ramp == power_plant_1.max_power


def test_powerplant_execute_dispatch():
    index = pd.date_range("2022-01-01", periods=24, freq="h")
    forecaster = PowerplantForecaster(
        index=index,
        availability=1,
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    power_plant = PowerPlant(
        id="test_pp",
        unit_operator="test_operator",
        technology="coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=700,
        min_power=50,
        efficiency=0.5,
        fuel_type="lignite",
        ramp_down=100,
        ramp_up=200,
        min_operating_time=3,
        min_down_time=2,
        forecaster=forecaster,
    )
    # was running before
    assert power_plant.execute_current_dispatch(index[0], index[0])[0] == 0

    power_plant.outputs["energy"].loc[index] = [
        0,
        0,
        0,
        200,
        200,
        100,
        0,
        0,  # correct dispatch
        100,
        100,
        0,
        0,  # breaking min_operating_time
        100,
        0,
        200,
        200,  # breaking min_down_time
        200,
        500,
        600,
        700,  # breaking ramp_up constraint
        700,
        400,
        300,
        200,  # breaking ramp_down constraint
    ]
    assert (
        len(power_plant.execute_current_dispatch(start=index[0], end=index[-1])) == 24
    )
    assert all(
        power_plant.outputs["energy"].loc[index[0] : index[7]]
        == [0, 0, 0, 200, 200, 100, 0, 0]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[8] : index[11]] == [100, 100, 50, 0]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[12] : index[15]] == [0, 0, 200, 200]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[16] : index[19]] == [200, 400, 600, 700]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[20] : index[23]] == [700, 600, 500, 400]
    )

    # check combinations of constraints
    power_plant.outputs["energy"].loc[index] = [
        0,
        0,
        200,
        0,  # breaking min_operation_time and ramp_down
        0,
        0,
        500,
        750,  # breaking min_down_time and ramp_up
        400,
        20,
        320,
        200,  # ramp_down
        120,
        20,
        20,
        220,  # breaking min_power and ramp_down
        420,
        720,
        620,
        520,
        420,
        720,
        800,
        700,  # breaking max_power and ramp_up
    ]
    power_plant.execute_current_dispatch(start=index[0], end=index[-1])
    assert all(
        power_plant.outputs["energy"].loc[index[0] : index[3]] == [0, 0, 200, 100]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[4] : index[7]] == [50, 0, 0, 200]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[8] : index[11]] == [400, 300, 320, 220]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[12] : index[15]] == [120, 50, 50, 220]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[16] : index[19]] == [420, 620, 620, 520]
    )
    assert all(
        power_plant.outputs["energy"].loc[index[20] : index[23]] == [420, 620, 700, 700]
    )


def test_powerplant_min_feedback(power_plant_1, mock_market_config):
    """
    Test that powerplant works fine for multi market bidding.
    Has two bids which add up to be above the minimum power.
    Make sure that ramping is not enforced to early.
    """
    start = datetime(2022, 1, 1, 0)
    end = datetime(2022, 1, 1, 1)
    product_type = "energy"

    # start bidding by calculating min and max power
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )
    assert min_power[0] == 200
    assert max_power[0] == 1000
    assert power_plant_1.outputs[product_type].at[start] == 0

    orderbook = [
        {
            "start_time": start,
            "end_time": end,
            "only_hours": None,
            "price": 40,
            "accepted_price": 40,
            "accepted_volume": 100,
            # half of min_power
        }
    ]

    # min_power gets accepted by fictional market
    power_plant_1.set_dispatch_plan(mock_market_config, orderbook)

    # second market request for same interval
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )

    # we still need 100kw as a runtime requirement
    assert min_power[0] == 100
    # we can not bid the maximum anymore, because we already provide energy on the other market
    assert max_power[0] == 900

    orderbook = [
        {
            "start_time": start,
            "end_time": end,
            "only_hours": None,
            "price": 40,
            "accepted_price": 40,
            "accepted_volume": 200,
            # half of min_power
        }
    ]

    # min_power gets accepted
    power_plant_1.set_dispatch_plan(mock_market_config, orderbook)

    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )

    # we do not need additional min_power, as our runtime requirement is fulfilled
    assert min_power[0] == 0
    # we can not bid the maximum anymore, because we already provide energy on the other market
    assert max_power[0] == 700

    # this should not do anything here, as we are in our constraints
    power_plant_1.execute_current_dispatch(start, end)

    # second market request for next interval
    start = datetime(2022, 1, 1, 1)
    end = datetime(2022, 1, 1, 2)
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )

    # now we can bid max_power and need min_power again
    assert min_power[0] == 200
    assert max_power[0] == 1000


def test_powerplant_ramp_feedback(power_plant_1, mock_market_config):
    """
    Make sure that ramping is enforced when a accepted volume at the prior time
    is below the minimum power.
    """
    product_type = "energy"
    start = datetime(2022, 1, 1, 0)
    end = datetime(2022, 1, 1, 1)

    # start bidding by calculating min and max power
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )
    assert min_power[0] == 200
    assert max_power[0] == 1000
    assert power_plant_1.outputs[product_type].at[start] == 0

    orderbook = [
        {
            "start_time": start,
            "end_time": end,
            "only_hours": None,
            "price": 40,
            "accepted_price": 40,
            "accepted_volume": 100,
            # half of min_power
        }
    ]

    # min_power gets accepted by fictional market
    power_plant_1.set_dispatch_plan(mock_market_config, orderbook)

    # second market request for same interval
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )

    # we still need 100kw as a runtime requirement
    assert min_power[0] == 100
    # we can not bid the maximum anymore, because we already provide energy on the other market
    assert max_power[0] == 900

    power_plant_1.execute_current_dispatch(start, end)

    # second market request for next interval
    start = datetime(2022, 1, 1, 1)
    end = datetime(2022, 1, 1, 2)
    min_power, max_power = power_plant_1.calculate_min_max_power(
        start, end, product_type=product_type
    )

    # now we can bid max_power and need min_power again
    assert min_power[0] == 200
    assert max_power[0] == 1000


def test_initialising_invalid_powerplants():
    index = pd.date_range(
        start=datetime(2023, 7, 1),
        end=datetime(2023, 7, 2),
        freq="1h",
    )
    param_dict = {
        "id": "id",
        "unit_operator": "operator",
        "technology": "technology",
        "bidding_strategies": {},
        "forecaster": PowerplantForecaster(index=index),
        "max_power": 0.0,
    }
    with pytest.raises(ValueError, match="max_power=-10 must be >= 0 for unit id"):
        d = param_dict.copy()
        d["max_power"] = -10
        PowerPlant(**d)
    with pytest.raises(ValueError, match="min_power=-10 must be >= 0 for unit id"):
        d = param_dict.copy()
        d["min_power"] = -10
        PowerPlant(**d)
    with pytest.raises(
        ValueError, match="min_power=20 must be <= max_power=10 for unit id"
    ):
        d = param_dict.copy()
        d["max_power"] = 10
        d["min_power"] = 20
        PowerPlant(**d)
    with pytest.raises(
        ValueError, match="min_operating_time=-10 must be > 0 for unit id"
    ):
        d = param_dict.copy()
        d["min_operating_time"] = -10
        PowerPlant(**d)
    with pytest.raises(ValueError, match="min_down_time=-10 must be > 0 for unit id"):
        d = param_dict.copy()
        d["min_down_time"] = -10
        PowerPlant(**d)


def _position_plant(p0, upper, min_power=0, cap_neg=0, cap_pos=0):
    """Two-hour plant (max 1000 MW) holding position p0, ID availability -> upper MW."""
    index = pd.date_range("2022-01-01", periods=4, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        availability_forecast={"ID": [upper / 1000] * 4},
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    pp = PowerPlant(
        id="pos_pp",
        unit_operator="test_operator",
        technology="hard coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=min_power,
        efficiency=0.5,
        fuel_type="lignite",
        emission_factor=0.5,
        forecaster=forecaster,
    )
    pp.outputs["energy"].loc[index[0] : index[-1]] = p0
    pp.outputs["capacity_neg"].loc[index[0] : index[-1]] = cap_neg
    pp.outputs["capacity_pos"].loc[index[0] : index[-1]] = cap_pos
    return pp, index[:2]  # tests query the first two hours


def _correction(pp, index):
    return pp.calculate_position_correction(
        index[0], index[-1] + index.freq, market_id="ID"
    )


def test_correction_zero_when_feasible():
    pp, index = _position_plant(p0=150, upper=220, min_power=80)
    assert list(_correction(pp, index)) == [0, 0]


def test_correction_renewable_overcommitted():
    pp, index = _position_plant(p0=300, upper=220)
    assert list(_correction(pp, index)) == [-80, -80]


def test_correction_no_bid_when_partially_cleared():
    pp, index = _position_plant(p0=150, upper=220)
    assert list(_correction(pp, index)) == [0, 0]


def test_correction_whole_position_when_U_below_m():
    pp, index = _position_plant(p0=300, upper=50, min_power=80)
    assert list(_correction(pp, index)) == [-300, -300]


def test_correction_between_zero_and_min():
    pp, index = _position_plant(p0=20, upper=100, min_power=40)
    assert list(_correction(pp, index)) == [20, 20]
    pp, index = _position_plant(p0=20, upper=30, min_power=40)
    assert list(_correction(pp, index)) == [-20, -20]
    # P0 closer to zero than to m: drop to 0
    pp, index = _position_plant(p0=10, upper=100, min_power=40)
    assert list(_correction(pp, index)) == [-10, -10]


def test_correction_base_unit_default_is_zero():
    from assume.common.base import BaseUnit

    pp, index = _position_plant(p0=300, upper=220)
    out = BaseUnit.calculate_position_correction(
        pp, index[0], index[-1] + index.freq, "ID"
    )
    assert list(out) == [0, 0]


def test_min_max_market_aware_negative_min():
    pp, index = _position_plant(p0=150, upper=220, min_power=80)
    end = index[-1] + index.freq
    lo, hi = pp.calculate_min_max_power(index[0], end, market_id="ID")
    # P_c = 150: m - P_c = -70, U - P_c = 70 (no clipping)
    assert list(lo) == [-70, -70]
    assert list(hi) == [70, 70]

    # without market_id unchanged: additional range from realised availability
    lo0, hi0 = pp.calculate_min_max_power(index[0], end)
    assert list(hi0) == [850, 850]
    assert list(lo0) == [0, 0]

    # corrected position: P0 300 > U 220 -> P_c 220 -> (80-220, 0)
    pp, index = _position_plant(p0=300, upper=220, min_power=80)
    lo, hi = pp.calculate_min_max_power(
        index[0], index[-1] + index.freq, market_id="ID"
    )
    assert list(lo) == [-140, -140]
    assert list(hi) == [0, 0]

    # cannot run: U < m and P_c = 0
    pp, index = _position_plant(p0=300, upper=50, min_power=80)
    lo, hi = pp.calculate_min_max_power(
        index[0], index[-1] + index.freq, market_id="ID"
    )
    assert list(lo) == [0, 0]
    assert list(hi) == [0, 0]


def _ccgt_curve_loss(ratio):
    """Efficiency loss of the CCGT part-load curve (spec section 5.6)."""
    return (
        0.178749 * ratio**4
        - 0.653192 * ratio**3
        + 0.964704 * ratio**2
        - 0.805845 * ratio
        + 0.315584
    )


def _gas_plant(
    technology="combined cycle gas turbine",
    fuel_type="natural gas",
    partial_load_eff=True,
):
    """100 MW gas plant: fuel 25, co2 25, emission factor 0.2, efficiency 0.55."""
    index = pd.date_range("2022-01-01", periods=4, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        fuel_prices={fuel_type: 25, "co2": 25},
        market_prices={"EOM": 0},
    )
    return PowerPlant(
        id="gas_pp",
        unit_operator="test_operator",
        technology=technology,
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=100,
        min_power=40,
        efficiency=0.55,
        fuel_type=fuel_type,
        emission_factor=0.2,
        forecaster=forecaster,
        partial_load_eff=partial_load_eff,
    )


def test_partial_load_eff_activates_curve():
    pp = _gas_plant()
    t = pp.index[0]
    assert pp.marginal_cost is None
    # full load: no efficiency loss -> (25 + 25 * 0.2) / 0.55
    assert pp.calculate_marginal_cost(t, 100) == pytest.approx(30 / 0.55)
    assert pp.calculate_marginal_cost(t, 100) == pytest.approx(54.545, abs=1e-3)
    # min load is more expensive per MWh
    mc_min = pp.calculate_marginal_cost(t, 40)
    assert mc_min == pytest.approx(30 / (0.55 - _ccgt_curve_loss(0.4)))
    assert mc_min == pytest.approx(68.24, abs=1e-2)
    assert mc_min > pp.calculate_marginal_cost(t, 70) > 54.545

    # without partial_load_eff the cost is flat
    flat = _gas_plant(partial_load_eff=False)
    assert flat.marginal_cost is not None
    assert flat.calculate_marginal_cost(t, 40) == pytest.approx(30 / 0.55)
    assert flat.calculate_marginal_cost(t, 100) == pytest.approx(30 / 0.55)


def test_curve_selected_by_technology_then_fuel():
    t = datetime(2022, 1, 1)
    # gas plant whose fuel is "natural gas": the curve comes from the technology
    ccgt = _gas_plant(technology="combined cycle gas turbine")
    assert ccgt.calculate_marginal_cost(t, 40) == pytest.approx(
        30 / (0.55 - _ccgt_curve_loss(0.4))
    )

    ocgt = _gas_plant(technology="open cycle gas turbine")
    loss = (
        0.485049 * 0.4**4
        - 1.540723 * 0.4**3
        + 1.899607 * 0.4**2
        - 1.251502 * 0.4
        + 0.407569
    )
    assert ocgt.calculate_marginal_cost(t, 40) == pytest.approx(30 / (0.55 - loss))

    coal_loss = (
        0.095859 * 0.4**4
        - 0.356010 * 0.4**3
        + 0.532948 * 0.4**2
        - 0.447059 * 0.4
        + 0.174262
    )
    # technology "hard coal" with an unrelated fuel name
    coal_tech = _gas_plant(technology="hard coal", fuel_type="coal")
    assert coal_tech.calculate_marginal_cost(t, 40) == pytest.approx(
        30 / (0.55 - coal_loss)
    )
    # fuel-type fallback (today's behaviour) for lignite
    lignite = _gas_plant(technology="steam turbine", fuel_type="lignite")
    assert lignite.calculate_marginal_cost(t, 40) == pytest.approx(
        30 / (0.55 - coal_loss)
    )
    # no curve: flat
    other = _gas_plant(technology="gas engine", fuel_type="natural gas")
    assert other.calculate_marginal_cost(t, 40) == pytest.approx(30 / 0.55)


def test_chord_cost_matches_hand_value():
    pp = _gas_plant()
    t = pp.index[0]
    cost_40 = 40 * 30 / (0.55 - _ccgt_curve_loss(0.4))
    cost_70 = 70 * 30 / (0.55 - _ccgt_curve_loss(0.7))
    cost_100 = 100 * 30 / 0.55

    assert pp.calculate_cost(t, 40) == pytest.approx(cost_40)
    assert pp.calculate_cost(t, 100) == pytest.approx(cost_100)
    assert pp.calculate_cost(t, 0) == 0.0
    assert pp.calculate_cost(t, -5) == 0.0

    assert pp.calculate_chord_cost(t, 40, 100) == pytest.approx(
        (cost_100 - cost_40) / 60
    )
    # spec reference values: p_mU ~ 45.4, chord 40->70 ~ 47.1, chord 70->100 ~ 43.7
    assert pp.calculate_chord_cost(t, 40, 100) == pytest.approx(45.4, abs=0.05)
    assert pp.calculate_chord_cost(t, 40, 70) == pytest.approx(47.1, abs=0.05)
    assert pp.calculate_chord_cost(t, 70, 100) == pytest.approx(43.7, abs=0.05)
    assert pp.calculate_chord_cost(t, 70, 40) == pytest.approx((cost_70 - cost_40) / 30)
    # degenerate chord falls back to the marginal (average) cost at that point
    assert pp.calculate_chord_cost(t, 70, 70) == pytest.approx(
        pp.calculate_marginal_cost(t, 70)
    )


if __name__ == "__main__":
    # run pytest and enable prints
    pytest.main(["-s", __file__])


def _startup_plant(index_len=10):
    index = pd.date_range("2022-01-01", periods=index_len, freq="h")
    forecaster = PowerplantForecaster(
        index,
        availability=1,
        fuel_prices={"lignite": 10, "co2": 10},
        market_prices={"EOM": 0},
    )
    return PowerPlant(
        id="su_pp",
        unit_operator="test_operator",
        technology="hard coal",
        bidding_strategies={"EOM": EnergyNaiveStrategy()},
        index=forecaster.index,
        max_power=1000,
        min_power=0,
        efficiency=0.5,
        additional_cost=10,
        fuel_type="lignite",
        forecaster=forecaster,
        emission_factor=0.5,
        hot_start_cost=1,
        warm_start_cost=2,
        cold_start_cost=3,
        downtime_hot_start=2,
        downtime_warm_start=4,
    )


def _dispatch(unit, market_id, hour, volume, price=50):
    from assume.common.market_objects import MarketConfig

    mc = MarketConfig(market_id=market_id, product_type="energy")
    order = {
        "start_time": unit.index[hour],
        "end_time": unit.index[hour] + unit.index.freq,
        "accepted_volume": volume,
        "accepted_price": price,
        "market_id": market_id,
    }
    unit.set_dispatch_plan(mc, [order])
    unit.calculate_cashflow("energy", [order])


def test_operating_cost_includes_startups():
    unit = _startup_plant()
    _dispatch(unit, "DA", 4, 500)
    cost = unit.calculate_operating_cost(unit.index[0], unit.index[-1])
    mc = unit.calculate_marginal_cost(unit.index[4], 500)
    # off for hours 0-3 (4 steps) -> warm start (> hot 2, <= warm 4)
    assert cost[4] == pytest.approx(500 * mc + unit.get_starting_costs(-4))
    assert unit.get_starting_costs(-4) == unit.warm_start_cost
    assert cost[5] == 0
    # continuing operation adds no start-up cost
    _dispatch(unit, "DA", 5, 500)
    cost = unit.calculate_operating_cost(unit.index[0], unit.index[-1])
    assert cost[5] == pytest.approx(500 * mc)


def test_attribution_catches_distant_start_reclassification():
    unit = _startup_plant()
    _dispatch(unit, "DA", 8, 500)  # long off period 0-7 -> cold start at 8
    cold = unit.outputs["energy_total_costs_DA"][unit.index[8]]
    assert cold == pytest.approx(
        500 * unit.calculate_marginal_cost(unit.index[8], 500) + unit.cold_start_cost
    )
    # ID adds running at hour 5: a new cold start there (5 off steps), and the
    # start at hour 8 is reclassified from cold to hot (only 2 off steps left)
    _dispatch(unit, "ID", 5, 500)
    costs_id = unit.outputs["energy_total_costs_ID"]
    assert costs_id[unit.index[5]] == pytest.approx(
        500 * unit.calculate_marginal_cost(unit.index[5], 500) + unit.cold_start_cost
    )
    assert costs_id[unit.index[8]] == pytest.approx(
        unit.hot_start_cost - unit.cold_start_cost
    )
    total = sum(
        unit.outputs[f"energy_total_costs_{m}"].data.sum() for m in ("DA", "ID")
    )
    final = unit.calculate_operating_cost(unit.index[0], unit.index[-1]).sum()
    assert total == pytest.approx(final)

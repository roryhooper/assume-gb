# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import pandas as pd
import pytest

from assume.common.exceptions import ValidationError
from assume.common.forecaster import DemandForecaster, PowerplantForecaster


def test_get_availability_vintages():
    idx = pd.date_range("2019-01-01", periods=3, freq="h")
    f = PowerplantForecaster(
        idx, availability=[1.0, 0.5, 0.2], availability_vintages={"da": [0.9, 0.6, 0.1]}
    )
    assert list(f.get_availability("da")) == [0.9, 0.6, 0.1]
    assert list(f.get_availability("actual")) == [1.0, 0.5, 0.2]
    assert list(f.get_availability("id")) == [1.0, 0.5, 0.2]  # fallback


def test_get_demand_vintages_and_validation():
    idx = pd.date_range("2019-01-01", periods=3, freq="h")
    f = DemandForecaster(
        idx, demand=[-10, -20, -30], demand_vintages={"id": [-11, -19, -31]}
    )
    assert list(f.get_demand("id")) == [-11, -19, -31]
    assert list(f.get_demand("da")) == [-10, -20, -30]
    with pytest.raises(ValidationError):
        DemandForecaster(idx, demand=[-1, -1, -1], demand_vintages={"da": [5, -1, -1]})


def test_availability_vintage_validation():
    idx = pd.date_range("2019-01-01", periods=3, freq="h")
    with pytest.raises(ValidationError):
        PowerplantForecaster(idx, availability_vintages={"da": [1.5, 0.5, 0.5]})

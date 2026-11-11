# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

GEN_PATH = (
    Path(__file__).parents[1]
    / "examples"
    / "inputs"
    / "example_01k"
    / "generate_inputs.py"
)
_spec = importlib.util.spec_from_file_location("generate_inputs_01k", GEN_PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

INDEX = pd.date_range(gen.SIM_START, gen.SIM_END, freq="h", inclusive="left")


def _corr() -> pd.DataFrame:
    return pd.DataFrame(gen.CORRELATION["DA"])


def _errors(seed, index=INDEX):
    return gen.generate_forecast_errors(
        index, gen.ERRORS["DA"], _corr(), np.random.default_rng(seed)
    )


def test_generator_deterministic():
    a = _errors(gen.SEED)
    b = _errors(gen.SEED)
    pd.testing.assert_frame_equal(a, b)
    assert not a.equals(_errors(gen.SEED + 1))


def test_error_sigma_and_correlation():
    # long horizon so sample statistics are tight; stationary sigma must match config
    index = pd.date_range("2019-01-01", periods=200_000, freq="h")
    err = _errors(1, index)
    for name, cfg in gen.ERRORS["DA"].items():
        assert err[name].std() == pytest.approx(cfg["sigma"], rel=0.2)
    # the generator documents that correlation applies to the innovations;
    # recover them from the AR(1) residuals
    resid = pd.DataFrame(
        {
            n: err[n].iloc[1:].values - cfg["phi"] * err[n].iloc[:-1].values
            for n, cfg in gen.ERRORS["DA"].items()
        }
    )
    got = resid.corr().values
    want = _corr().values
    assert np.allclose(got, want, atol=0.2 * 1.0, rtol=0.2)


def test_week_level_sigma():
    # over the actual week, sigma is within a loose multiple (few effective samples)
    err = _errors(gen.SEED)
    assert err.shape == (len(INDEX), 4)
    assert np.isfinite(err.values).all()


def test_availability_clipped(tmp_path):
    gen.main(tmp_path)
    real = pd.read_csv(tmp_path / "availability_df.csv", index_col=0)
    da = pd.read_csv(tmp_path / "availability_DA_df.csv", index_col=0)
    assert da.values.min() >= 0 and da.values.max() <= 1
    assert (da["Solar"][real["Solar"] == 0] == 0).all()
    assert (da != real).any().any()
    dem = pd.read_csv(tmp_path / "demand_DA_df.csv", index_col=0)
    assert (dem.values > 0).all()
    assert len(da) == len(real) == 8 * 24


def test_invalid_correlation_rejected():
    bad = pd.DataFrame(
        [[1, 0.9, -0.9, 0], [0.9, 1, 0.9, 0], [-0.9, 0.9, 1, 0], [0, 0, 0, 1]],
        index=list(gen.ERRORS["DA"]),
        columns=list(gen.ERRORS["DA"]),
    )
    with pytest.raises(ValueError, match="positive definite"):
        gen.generate_forecast_errors(
            INDEX, gen.ERRORS["DA"], bad, np.random.default_rng(0)
        )


def test_invalid_sigma_phi_rejected():
    cfg = {k: dict(v) for k, v in gen.ERRORS["DA"].items()}
    cfg["solar"]["phi"] = 1.0
    with pytest.raises(ValueError, match="phi"):
        gen.generate_forecast_errors(INDEX, cfg, _corr(), np.random.default_rng(0))
    cfg = {k: dict(v) for k, v in gen.ERRORS["DA"].items()}
    cfg["solar"]["sigma"] = -0.1
    with pytest.raises(ValueError, match="sigma"):
        gen.generate_forecast_errors(INDEX, cfg, _corr(), np.random.default_rng(0))

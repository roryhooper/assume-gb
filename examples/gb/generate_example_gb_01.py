# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

"""
Generate the time series of the GB sequential market example ``example_gb_01``.

``config.yaml``, the unit tables and ``fuel_prices_df.csv`` in
``examples/inputs/example_gb_01`` are maintained by hand. This script only
writes the time series that depend on them.

One week of 2019 wind and solar availability and demand is taken from
``example_01c`` (the period between ``start_date`` and ``end_date`` in
``config.yaml``), resampled to hourly means and demand scaled to a 1000 MW peak.

The actual series are written to ``availability_df.csv`` / ``demand_df.csv``.
Day-ahead (DA) and intraday (ID) forecast vintages are derived from the actual
series with an AR(1) relative error:

- ``eps_da[t] = rho * eps_da[t-1] + N(0, sigma_da * sqrt(1 - rho**2))``
- DA forecast = actual * (1 + eps_da)
- ID forecast = actual * (1 + lam * eps_da + N(0, sigma_id))

Availability vintages are clipped to [0, 1], solar vintages are 0 wherever the
actual is 0. ``forecasts_df.csv`` holds a DA price forecast (``price_DA``) from
a merit order of the units in ``powerplant_units.csv`` on the DA vintage, capped
at the DA ``maximum_bid_price``.

If ``--out`` is not the example folder, the hand-maintained files are copied
there too, so it holds a complete scenario.

Usage::

    python examples/gb/generate_example_gb_01.py [--seed 42] [--sigma-da 0.15]
        [--sigma-id 0.05] [--rho 0.8] [--lam 0.3] [--out examples/inputs/example_gb_01]
"""

import argparse
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "examples" / "inputs" / "example_01c"
EXAMPLE_DIR = REPO_ROOT / "examples" / "inputs" / "example_gb_01"
LICENSE_TEMPLATE = SOURCE_DIR / "demand_df.csv.license"

STATIC_FILES = [
    "config.yaml",
    "powerplant_units.csv",
    "storage_units.csv",
    "demand_units.csv",
    "fuel_prices_df.csv",
]

DEMAND_PEAK = 1000.0


def load_shapes(
    start: pd.Timestamp, end: pd.Timestamp
) -> tuple[pd.DataFrame, pd.Series]:
    """Hourly actual availability (wind, solar) and demand (MW, 1000 MW peak)."""
    availability = pd.read_csv(
        SOURCE_DIR / "availability_df.csv", index_col=0, parse_dates=True
    )
    demand = pd.read_csv(SOURCE_DIR / "demand_df.csv", index_col=0, parse_dates=True)

    availability = (
        availability.loc[start:end, ["Wind Onshore", "Solar"]].resample("1h").mean()
    )
    availability.columns = ["wind", "solar"]
    availability = availability.clip(0.0, 1.0)

    demand = demand.loc[start:end, "demand_EOM"].resample("1h").mean()
    demand = demand / demand.max() * DEMAND_PEAK
    return availability, demand


def ar1_errors(
    rng: np.random.Generator, n: int, sigma: float, rho: float
) -> np.ndarray:
    """Stationary AR(1) process with marginal standard deviation ``sigma``."""
    eps = np.empty(n)
    eps[0] = rng.normal(0.0, sigma)
    innovation = sigma * np.sqrt(1.0 - rho**2)
    for t in range(1, n):
        eps[t] = rho * eps[t - 1] + rng.normal(0.0, innovation)
    return eps


def make_vintages(
    actual: pd.Series,
    rng: np.random.Generator,
    sigma_da: float,
    sigma_id: float,
    rho: float,
    lam: float,
) -> tuple[pd.Series, pd.Series]:
    """DA and ID forecast vintages of ``actual`` with relative errors."""
    n = len(actual)
    eps_da = ar1_errors(rng, n, sigma_da, rho)
    eps_id = rng.normal(0.0, sigma_id, n)
    da = actual * (1.0 + eps_da)
    id_ = actual * (1.0 + lam * eps_da + eps_id)
    return da, id_


def price_forecast(
    demand_da: pd.Series,
    wind_da: pd.Series,
    solar_da: pd.Series,
    powerplants: pd.DataFrame,
    fuel_prices: pd.Series,
    price_cap: float,
) -> pd.Series:
    """Merit order price on the DA vintage: 0 (RES), gas mc, or the price cap."""
    wind_mw = powerplants.at["wind", "max_power"]
    solar_mw = powerplants.at["solar", "max_power"]
    gas = powerplants.loc["gas"]
    gas_mc = (
        fuel_prices[gas["fuel_type"]] / gas["efficiency"]
        + fuel_prices["co2"] * gas["emission_factor"] / gas["efficiency"]
        + gas["additional_cost"]
    )

    residual_load = demand_da - wind_da * wind_mw - solar_da * solar_mw
    price = np.where(
        residual_load <= 0.0,
        0.0,
        np.where(residual_load <= gas["max_power"], gas_mc, price_cap),
    )
    return pd.Series(price, index=demand_da.index, name="price_DA")


def write_csv(
    df: pd.DataFrame, path: Path, index: bool = True, float_format: str | None = None
) -> None:
    df.to_csv(path, index=index, float_format=float_format)
    shutil.copyfile(LICENSE_TEMPLATE, path.with_name(path.name + ".license"))


def generate(
    out: Path,
    seed: int = 42,
    sigma_da: float = 0.15,
    sigma_id: float = 0.05,
    rho: float = 0.8,
    lam: float = 0.3,
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if out.resolve() != EXAMPLE_DIR.resolve():
        for name in STATIC_FILES:
            shutil.copyfile(EXAMPLE_DIR / name, out / name)
            if name.endswith(".csv"):
                shutil.copyfile(
                    EXAMPLE_DIR / f"{name}.license", out / f"{name}.license"
                )

    with open(EXAMPLE_DIR / "config.yaml") as f:
        config = yaml.safe_load(f)["base"]
    powerplants = pd.read_csv(EXAMPLE_DIR / "powerplant_units.csv", index_col="name")
    fuel_prices = pd.read_csv(EXAMPLE_DIR / "fuel_prices_df.csv", index_col="fuel").loc[
        "price"
    ]
    price_cap = config["markets_config"]["DA"]["maximum_bid_price"]

    rng = np.random.default_rng(seed)
    availability, demand = load_shapes(
        pd.Timestamp(config["start_date"]), pd.Timestamp(config["end_date"])
    )

    wind_da, wind_id = make_vintages(
        availability["wind"], rng, sigma_da, sigma_id, rho, lam
    )
    solar_da, solar_id = make_vintages(
        availability["solar"], rng, sigma_da, sigma_id, rho, lam
    )
    demand_da, demand_id = make_vintages(demand, rng, sigma_da, sigma_id, rho, lam)

    wind_da, wind_id = wind_da.clip(0.0, 1.0), wind_id.clip(0.0, 1.0)
    solar_dark = availability["solar"] <= 0.0
    solar_da = solar_da.clip(0.0, 1.0).mask(solar_dark, 0.0)
    solar_id = solar_id.clip(0.0, 1.0).mask(solar_dark, 0.0)
    demand_da, demand_id = demand_da.clip(lower=0.0), demand_id.clip(lower=0.0)

    series = {
        "availability_df": {
            "wind": availability["wind"],
            "solar": availability["solar"],
        },
        "availability_da_df": {"wind": wind_da, "solar": solar_da},
        "availability_id_df": {"wind": wind_id, "solar": solar_id},
        "demand_df": {"demand": demand},
        "demand_da_df": {"demand": demand_da},
        "demand_id_df": {"demand": demand_id},
        "forecasts_df": {
            "price_DA": price_forecast(
                demand_da, wind_da, solar_da, powerplants, fuel_prices, price_cap
            )
        },
    }
    for name, columns in series.items():
        df = pd.DataFrame(columns)
        df.index.name = "datetime"
        write_csv(df, out / f"{name}.csv", float_format="%.6f")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sigma-da", type=float, default=0.15)
    parser.add_argument("--sigma-id", type=float, default=0.05)
    parser.add_argument("--rho", type=float, default=0.8)
    parser.add_argument("--lam", type=float, default=0.3)
    parser.add_argument("--out", type=Path, default=EXAMPLE_DIR)
    args = parser.parse_args()
    generate(
        out=args.out,
        seed=args.seed,
        sigma_da=args.sigma_da,
        sigma_id=args.sigma_id,
        rho=args.rho,
        lam=args.lam,
    )


if __name__ == "__main__":
    main()

# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

"""
Seeded input generator for the example scenario ``example_01k``.

Builds a small sequential Day-Ahead (DA) / Intraday (ID) scenario from the German
2019 data of ``example_03``, scaled so that the hourly demand peak is
``PEAK_DEMAND_MW``. Besides the realised time series it writes per-market
forecasts for DA: the realised wind/solar availability and demand perturbed by
correlated AR(1) forecast errors. ID has no entry in ``ERRORS`` and therefore
falls back to the realised series (it forecasts perfectly).

Run with ``python examples/inputs/example_01k/generate_inputs.py``.
"""

from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- configuration
SEED = 2019
SIM_START = "2019-03-17"
SIM_END = "2019-03-25"
PEAK_DEMAND_MW = 1000
SOURCE = "example_03"

# Forecast error model per market, per series.
#   sigma: stationary standard deviation of the error
#          wind_*: fraction of capacity (additive on availability)
#          solar, demand: relative to the realised value (multiplicative)
#   phi:   AR(1) persistence from hour to hour
# NOTE: sigma, phi and CORRELATION are uncalibrated assumptions based on physical
# reasoning, not fitted to forecast-error data.
ERRORS = {
    "DA": {
        "wind_onshore": {"sigma": 0.06, "phi": 0.9},
        "wind_offshore": {"sigma": 0.06, "phi": 0.9},
        "solar": {"sigma": 0.04, "phi": 0.8},
        "demand": {"sigma": 0.015, "phi": 0.9},
    }
}

# Correlation of the error innovations (assumptions):
# - onshore and offshore wind share weather systems;
# - wind and solar are only weakly related through fronts;
# - rooftop and local solar lowers net demand;
# - wind chill raises heating load.
_SERIES = ["wind_onshore", "wind_offshore", "solar", "demand"]
CORRELATION = {
    "DA": {
        "wind_onshore": {
            "wind_onshore": 1.0,
            "wind_offshore": 0.6,
            "solar": -0.1,
            "demand": 0.1,
        },
        "wind_offshore": {
            "wind_onshore": 0.6,
            "wind_offshore": 1.0,
            "solar": -0.1,
            "demand": 0.1,
        },
        "solar": {
            "wind_onshore": -0.1,
            "wind_offshore": -0.1,
            "solar": 1.0,
            "demand": -0.3,
        },
        "demand": {
            "wind_onshore": 0.1,
            "wind_offshore": 0.1,
            "solar": -0.3,
            "demand": 1.0,
        },
    }
}

DEMAND_UNIT = "demand_DE"
INPUTS_ROOT = Path(__file__).resolve().parents[1]

# name, technology, fuel, MW, efficiency, min_power ratio, ramp ratio, additional cost,
# hot/warm/cold start cost, min operating time, min down time, emission factor, operator
THERMAL = [
    ("Gas 1", "combined cycle gas turbine", "natural gas", 94, 0.58, 0.41, 0.84, 3.5,
     (24.1, 34.2, 46.7), 5, 3, 0.201, "Operator-Gas"),
    ("Gas 2", "combined cycle gas turbine", "natural gas", 94, 0.55, 0.41, 0.84, 3.5,
     (24.1, 34.2, 46.7), 5, 3, 0.201, "Operator-Gas"),
    ("Gas 3", "combined cycle gas turbine", "natural gas", 93, 0.52, 0.41, 0.84, 3.5,
     (24.1, 34.2, 46.7), 5, 3, 0.201, "Operator-Gas"),
    ("Hard coal", "hard coal", "hard coal", 321, 0.41, 0.41, 0.69, 1.3,
     (32.7, 51.6, 69.9), 7, 6, 0.335, "Operator-Coal"),
    ("Lignite", "lignite", "lignite", 271, 0.39, 0.50, 0.53, 1.65,
     (32.5, 51.3, 69.9), 10, 7, 0.406, "Operator-Coal"),
]  # fmt: skip
RENEWABLES = [
    ("Wind Onshore", "wind_onshore", 692),
    ("Wind Offshore", "wind_offshore", 98),
    ("Solar", "solar", 635),
]

CONFIG_YAML = """\
# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

base:
  start_date: 2019-03-17 00:00
  end_date: 2019-03-24 23:00
  time_step: 1h
  save_frequency_hours: 24

  markets_config:
    DA:
      operator: DA_operator
      product_type: energy
      start_date: 2019-03-17 09:00
      end_date: 2019-03-25 00:00
      products:
        - duration: 1h
          count: 24
          first_delivery: 15h
      opening_frequency: 24h
      opening_duration: 1h
      volume_unit: MWh
      price_unit: EUR/MWh
      maximum_bid_volume: 100000
      maximum_bid_price: 3000
      minimum_bid_price: -500
      market_mechanism: complex_clearing
      param_dict:
        solver_name: highs
        pricing_mechanism: pay_as_clear
      additional_fields: [bid_type, min_acceptance_ratio, parent_bid_id]
    ID:
      operator: ID_operator
      product_type: energy
      start_date: 2019-03-17 22:00
      end_date: 2019-03-25 00:00
      products:
        - duration: 1h
          count: 24
          first_delivery: 2h
      opening_frequency: 24h
      opening_duration: 1h
      volume_unit: MWh
      price_unit: EUR/MWh
      maximum_bid_volume: 100000
      maximum_bid_price: 3000
      minimum_bid_price: -500
      market_mechanism: complex_clearing
      param_dict:
        solver_name: highs
        pricing_mechanism: pay_as_clear
      additional_fields: [bid_type, min_acceptance_ratio, parent_bid_id]
"""


# ------------------------------------------------------------------- validation
def _validate(errors_cfg: dict, correlation: pd.DataFrame) -> None:
    names = list(errors_cfg)
    for name, cfg in errors_cfg.items():
        if not cfg["sigma"] >= 0:
            raise ValueError(
                f"Invalid sigma for '{name}': {cfg['sigma']} (must be >= 0)"
            )
        if not 0 <= cfg["phi"] < 1:
            raise ValueError(
                f"Invalid phi for '{name}': {cfg['phi']} (must be in [0, 1))"
            )
    if set(correlation.index) != set(names) or set(correlation.columns) != set(names):
        raise ValueError(
            "Correlation matrix must be indexed by the same series as errors_cfg"
        )
    corr = correlation.loc[names, names].to_numpy(dtype=float)
    if not np.allclose(corr, corr.T):
        raise ValueError("Correlation matrix is not symmetric")
    if not np.allclose(np.diag(corr), 1.0):
        raise ValueError("Correlation matrix must have a unit diagonal")
    if np.linalg.eigvalsh(corr).min() <= 1e-10:
        raise ValueError("Correlation matrix is not positive definite")


def generate_forecast_errors(
    index: pd.DatetimeIndex,
    errors_cfg: dict,
    correlation: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Simulate correlated AR(1) forecast errors, e_t = phi * e_{t-1} + L @ eps_t.

    eps_t ~ N(0, I). L is the Cholesky factor of the correlation matrix, scaled per
    series by sigma * sqrt(1 - phi^2) so that each series has stationary standard
    deviation sigma. The first value is drawn from the stationary distribution.

    Raises:
        ValueError: if sigma/phi are out of range or the correlation is invalid.
    """
    _validate(errors_cfg, correlation)
    names = list(errors_cfg)
    sigma = np.array([errors_cfg[n]["sigma"] for n in names])
    phi = np.array([errors_cfg[n]["phi"] for n in names])
    chol = np.linalg.cholesky(correlation.loc[names, names].to_numpy(dtype=float))
    chol_innov = (sigma * np.sqrt(1 - phi**2))[:, None] * chol
    chol_start = sigma[:, None] * chol

    eps = rng.standard_normal((len(index), len(names)))
    e = np.zeros((len(index), len(names)))
    e[0] = chol_start @ eps[0]
    for t in range(1, len(index)):
        e[t] = phi * e[t - 1] + chol_innov @ eps[t]
    return pd.DataFrame(e, index=index, columns=names)


# ------------------------------------------------------------------------ main
def _hourly(df: pd.DataFrame) -> pd.DataFrame:
    df.index = pd.to_datetime(df.index)
    return df.resample("h").mean()


def _write(df: pd.DataFrame, path: Path) -> None:
    df.index.name = "datetime"
    df.to_csv(path)


def _unit_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rows = []
    for name, tech, mw in RENEWABLES:
        rows.append(
            {
                "name": name,
                "technology": tech,
                "bidding_DA": "powerplant_energy_naive",
                "bidding_ID": "powerplant_energy_naive",
                "fuel_type": "renewable",
                "emission_factor": 0,
                "max_power": mw,
                "min_power": 0,
                "efficiency": 1,
                "additional_cost": 0,
                "hot_start_cost": 0,
                "warm_start_cost": 0,
                "cold_start_cost": 0,
                "min_operating_time": 0,
                "min_down_time": 0,
                "unit_operator": "Operator-RE",
            }
        )
    strat = "powerplant_energy_heuristic_flexable_hourly_linked"
    for (
        name,
        tech,
        fuel,
        mw,
        eff,
        pmin,
        ramp,
        add,
        starts,
        t_on,
        t_off,
        ef,
        op,
    ) in THERMAL:
        rows.append(
            {
                "name": name,
                "technology": tech,
                "bidding_DA": strat,
                "bidding_ID": strat,
                "fuel_type": fuel,
                "emission_factor": ef,
                "max_power": mw,
                "min_power": round(pmin * mw, 2),
                "efficiency": eff,
                "ramp_up": round(ramp * mw, 2),
                "ramp_down": round(ramp * mw, 2),
                "additional_cost": add,
                "hot_start_cost": starts[0],
                "warm_start_cost": starts[1],
                "cold_start_cost": starts[2],
                "min_operating_time": t_on,
                "min_down_time": t_off,
                "downtime_hot_start": 8,
                "downtime_warm_start": 48,
                "partial_load_eff": True,
                "unit_operator": op,
            }
        )
    plants = pd.DataFrame(rows)

    storage = pd.DataFrame(
        [
            {
                "name": "Battery",
                "technology": "li-ion_battery",
                "bidding_DA": "storage_energy_heuristic_flexable",
                "bidding_ID": "storage_energy_heuristic_flexable",
                "max_power_charge": 100,
                "max_power_discharge": 90,
                "capacity": 190,
                "efficiency_charge": 0.95,
                "efficiency_discharge": 0.95,
                "additional_cost_charge": 5,
                "additional_cost_discharge": 5,
                "min_soc": 0,
                "max_soc": 1,
                "initial_soc": 0.5,
                "unit_operator": "Operator-Storage",
            }
        ]
    )
    demand = pd.DataFrame(
        [
            {
                "name": DEMAND_UNIT,
                "technology": "inflex_demand",
                "bidding_DA": "demand_energy_naive",
                "bidding_ID": "demand_energy_naive",
                "max_power": -PEAK_DEMAND_MW,
                "min_power": 0,
                "price": 3000,
                "unit_operator": "Operator-Demand",
            }
        ]
    )
    return plants, storage, demand


def main(out_dir: Path | None = None) -> None:
    out_dir = Path(out_dir) if out_dir is not None else Path(__file__).resolve().parent
    out_dir.mkdir(parents=True, exist_ok=True)
    src = INPUTS_ROOT / SOURCE

    demand_full = _hourly(
        pd.read_csv(
            src / "demand_df.csv", index_col=0, usecols=["datetime", "demand_EOM"]
        )
    )["demand_EOM"]
    k = PEAK_DEMAND_MW / demand_full.max()
    avail = _hourly(
        pd.read_csv(
            src / "availability_df.csv",
            index_col=0,
            usecols=["datetime", "Wind Onshore", "Wind Offshore", "Solar"],
        )
    )
    fuel = _hourly(pd.read_csv(src / "fuel_prices_df.csv", index_col=0))

    window = slice(SIM_START, pd.Timestamp(SIM_END) - pd.Timedelta(hours=1))
    demand = (demand_full * k).loc[window].to_frame(DEMAND_UNIT)
    avail = avail.loc[window]
    fuel = fuel.loc[window]
    _write(demand, out_dir / "demand_df.csv")
    _write(avail, out_dir / "availability_df.csv")
    _write(fuel, out_dir / "fuel_prices_df.csv")

    plants, storage, demand_units = _unit_tables()
    plants.to_csv(out_dir / "powerplant_units.csv", index=False)
    storage.to_csv(out_dir / "storage_units.csv", index=False)
    demand_units.to_csv(out_dir / "demand_units.csv", index=False)
    (out_dir / "config.yaml").write_text(CONFIG_YAML)

    rng = np.random.default_rng(SEED)
    for market, errors_cfg in ERRORS.items():
        corr = pd.DataFrame(CORRELATION[market]).loc[list(errors_cfg), list(errors_cfg)]
        err = generate_forecast_errors(demand.index, errors_cfg, corr, rng)
        av = avail.copy()
        av["Wind Onshore"] = (avail["Wind Onshore"] + err["wind_onshore"]).clip(0, 1)
        av["Wind Offshore"] = (avail["Wind Offshore"] + err["wind_offshore"]).clip(0, 1)
        av["Solar"] = (avail["Solar"] * (1 + err["solar"])).clip(0, 1)
        _write(av, out_dir / f"availability_{market}_df.csv")
        dem = demand.mul(1 + err["demand"], axis=0)
        _write(dem, out_dir / f"demand_{market}_df.csv")


if __name__ == "__main__":
    main()

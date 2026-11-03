# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

"""End-to-end tests of the GB sequential market example (DA -> ID -> BAL -> settlement)."""

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from assume import World
from assume.common.utils import get_available_products
from assume.scenario.loader_csv import load_scenario_folder, make_market_config

INPUTS = Path("examples/inputs")
SCENARIO = "example_gb_01"
GENERATOR = Path("examples/gb/generate_example_gb_01.py")


def run_scenario(inputs_path: str, db: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    world = World(database_uri=db)
    load_scenario_folder(world, inputs_path, SCENARIO, "base")
    world.run()
    meta = pd.read_sql("select * from market_meta where market_id='BAL'", db)
    disp = pd.read_sql("select * from unit_dispatch", db)
    return meta, disp


def test_gb_market_products_start_d_plus_1():
    with open(INPUTS / SCENARIO / "config.yaml") as f:
        config = yaml.safe_load(f)["base"]
    world_start = pd.Timestamp(config["start_date"])
    world_end = pd.Timestamp(config["end_date"])

    products = {}
    for market_id, params in config["markets_config"].items():
        market_config = make_market_config(market_id, params, world_start, world_end)
        products[market_id] = get_available_products(
            market_config.market_products, market_config.opening_hours[0]
        )

    da, id_, bal = products["DA"], products["ID"], products["BAL"]
    assert len(da) == 24
    assert da[0][0] == pd.Timestamp("2019-01-08 00:00")
    assert da[-1][1] == pd.Timestamp("2019-01-09 00:00")
    assert id_[0][0] == pd.Timestamp("2019-01-08 00:00")

    bal_config = make_market_config(
        "BAL", config["markets_config"]["BAL"], world_start, world_end
    )
    assert bal_config.opening_hours[0] == pd.Timestamp("2019-01-07 22:50")
    assert len(bal) == 1
    assert bal[0][0] == pd.Timestamp("2019-01-08 00:00")
    assert bal[0][1] == pd.Timestamp("2019-01-08 01:00")


@pytest.mark.slow
def test_gb_example_run_invariants(tmp_path):
    db = f"sqlite:///{tmp_path}/gb.db"
    world = World(database_uri=db)
    load_scenario_folder(world, "examples/inputs", "example_gb_01", "base")
    world.run()
    meta = pd.read_sql("select * from market_meta where market_id='BAL'", db)
    disp = pd.read_sql("select * from unit_dispatch", db)
    assert len(meta) == 168  # Review Focus 1
    assert pd.Timestamp(meta.product_start.min()) == pd.Timestamp("2019-01-08 00:00")
    ok = meta.assign(product_start=pd.to_datetime(meta.product_start)).set_index(
        "product_start"
    )
    sums = (
        disp.assign(time=pd.to_datetime(disp.time))
        .groupby("time")[["physical", "imbalance"]]
        .sum()
    )
    hours = ok.index.intersection(sums.index)
    assert len(hours) == 168
    assert len(disp) == len(disp.drop_duplicates(["time", "unit"]))
    full = hours[ok.loc[hours, "unmet_volume"].abs() < 1e-6]
    assert sums.loc[full, "physical"].abs().max() < 1e-6  # Σ physical = 0
    assert (
        sums.loc[hours, "imbalance"] - ok.loc[hours, "niv"]
    ).abs().max() < 1e-6  # Σ imbalance = NIV
    assert (
        (ok.up_volume + ok.down_volume) - ok.niv.abs() + ok.unmet_volume
    ).abs().max() < 1e-6


@pytest.mark.slow
def test_gb_example_perfect_forecast(tmp_path):
    """
    A sigma=0 run has no forecast error, so there is no NIV and no imbalance.

    The generator with zero errors makes the DA/ID vintages equal the actual
    series and also regenerates ``price_DA`` from the actual series, so the
    battery's DA price forecast is perfect too.
    """
    subprocess.run(
        [
            sys.executable,
            str(GENERATOR),
            "--sigma-da",
            "0",
            "--sigma-id",
            "0",
            "--out",
            str(tmp_path / SCENARIO),
        ],
        check=True,
    )

    meta, disp = run_scenario(str(tmp_path), f"sqlite:///{tmp_path}/gb.db")
    assert len(meta) == 168
    assert meta.niv.abs().max() < 1e-6
    assert disp.imbalance.abs().max() < 1e-6

# SPDX-FileCopyrightText: ASSUME Developers
#
# SPDX-License-Identifier: MIT

import shutil
from pathlib import Path

import pandas as pd
import pytest

from assume import World
from assume.scenario.loader_csv import (
    load_config_and_create_forecaster,
    setup_world,
)
from assume.units import PowerPlant

EXAMPLE = Path("examples/inputs/example_01c")


def _copy_example(tmp_path: Path) -> Path:
    dest = tmp_path / "example_01c"
    shutil.copytree(EXAMPLE, dest)
    return dest


def test_vintage_files_loaded(tmp_path):
    dest = _copy_example(tmp_path)
    actual = pd.read_csv(dest / "availability_df.csv", index_col=0, parse_dates=True)
    pd.DataFrame({"Wind Onshore": 0.123}, index=actual.index).to_csv(
        dest / "availability_da_df.csv"
    )

    data = load_config_and_create_forecaster(str(tmp_path), "example_01c", "eom_only")
    forecaster = data["unit_forecasts"]["Wind Onshore"]
    first_ts = actual.index[0]

    assert forecaster.get_availability("da").at[first_ts] == 0.123
    assert (
        forecaster.get_availability("id").at[first_ts]
        == forecaster.get_availability("actual").at[first_ts]
    )


def test_unit_types_mapping():
    class MyPP(PowerPlant):
        pass

    world = World()
    world.unit_types["my_pp"] = MyPP
    world.scenario_data = load_config_and_create_forecaster(
        "examples/inputs", "example_01c", "eom_only"
    )
    world.scenario_data["config"]["unit_types"] = {"power_plant": "my_pp"}
    setup_world(world=world)

    plants = [
        u
        for op in world.unit_operators.values()
        for u in op.units.values()
        if isinstance(u, PowerPlant)
    ]
    assert plants
    assert all(isinstance(u, MyPP) for u in plants)


def _gb_world(edit_config) -> World:
    world = World()
    world.scenario_data = load_config_and_create_forecaster(
        "examples/inputs", "example_gb_01", "base"
    )
    edit_config(world.scenario_data["config"])
    setup_world(world=world)
    return world


def test_gb_setup_is_valid():
    world = _gb_world(lambda config: None)
    world._validate_setup()


def test_settlement_market_requires_sequential_units():
    world = _gb_world(lambda config: config.pop("unit_types"))
    with pytest.raises(ValueError, match=r"Unit '\w+'.*market 'BAL'"):
        world._validate_setup()


def test_sequential_units_require_settlement_market():
    def drop_settlement(config):
        config["markets_config"]["BAL"]["param_dict"].pop("imbalance_settlement")

    world = _gb_world(drop_settlement)
    with pytest.raises(
        ValueError, match=r"Sequential unit '\w+'.*imbalance_settlement"
    ):
        world._validate_setup()

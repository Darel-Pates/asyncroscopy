import numpy as np
import pytest

from asyncroscopy.instruments.electron_microscope.hardware.corrector_digital_twin import (
    DEFAULT_ABERRATIONS,
)
from asyncroscopy.instruments.electron_microscope.polycrystalline_gold_digital_twin import (
    ceos_aberrations_to_probe_parameters,
    generate_polycrystalline_gold_slab,
)


def test_polycrystalline_volume_is_deterministic_and_contains_voids():
    kwargs = dict(
        voxel_size_nm=0.5,
        region_count=9,
        empty_region_fraction=0.25,
        minimum_gold_fraction=0.9,
        seed=41,
    )
    labels_a, potential_a, parameters_a = generate_polycrystalline_gold_slab(
        (12, 30, 30), **kwargs
    )
    labels_b, potential_b, parameters_b = generate_polycrystalline_gold_slab(
        (12, 30, 30), **kwargs
    )

    assert np.array_equal(labels_a, labels_b)
    assert np.array_equal(potential_a, potential_b)
    assert parameters_a == parameters_b
    assert labels_a.shape == potential_a.shape == (12, 30, 30)
    assert np.any(labels_a == 0)
    assert np.any(labels_a > 0)
    assert np.all(potential_a[labels_a == 0] == 0.0)
    assert np.all(potential_a[labels_a > 0] > 0.0)


def test_occupied_regions_have_orientation_and_au_rich_composition():
    _labels, _potential, parameters = generate_polycrystalline_gold_slab(
        (10, 24, 24),
        voxel_size_nm=0.5,
        region_count=8,
        empty_region_fraction=0.25,
        minimum_gold_fraction=0.92,
        seed=17,
    )

    occupied = [parameter for parameter in parameters if parameter.occupied]
    empty = [parameter for parameter in parameters if not parameter.occupied]
    assert occupied and empty
    for parameter in occupied:
        assert len(parameter.orientation_euler_deg) == 3
        assert parameter.composition["Au"] >= 0.92
        assert sum(parameter.composition.values()) == pytest.approx(1.0)
    for parameter in empty:
        assert parameter.orientation_euler_deg is None
        assert parameter.composition == {}


def test_ceos_coefficients_map_to_probe_units_and_defocus():
    coefficients = {key: list(values) for key, values in DEFAULT_ABERRATIONS.items()}
    coefficients["C1"] = [15e-9]

    probe_parameters = ceos_aberrations_to_probe_parameters(
        coefficients,
        fov_nm=80.0,
        acceleration_voltage_ev=200_000.0,
        convergence_angle_mrad=30.0,
    )

    assert probe_parameters["C10"] == pytest.approx(15.0)
    assert probe_parameters["C30"] == pytest.approx(123.0)
    assert probe_parameters["FOV"] == pytest.approx(80.0)
    assert probe_parameters["convergence_angle"] == pytest.approx(30.0)

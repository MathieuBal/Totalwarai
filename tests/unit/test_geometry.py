"""Primitives geometriques : distances, caps, arcs et repartition en ligne."""

from __future__ import annotations

import math

import pytest

from totalwar_ai.domain.geometry import (
    Vector3,
    angle_between,
    centroid,
    is_in_rear_arc,
    spread_positions,
)
from totalwar_ai.domain.serialization import SchemaError


def test_distance_2d_ignore_altitude() -> None:
    a = Vector3(0.0, 100.0, 0.0)
    b = Vector3(3.0, -50.0, 4.0)
    assert a.distance_2d(b) == pytest.approx(5.0)
    assert a.distance_to(b) > 5.0


def test_direction_to_est_unitaire() -> None:
    direction = Vector3().direction_to(Vector3(10.0, 0.0, 10.0))
    assert direction.length_2d() == pytest.approx(1.0)


def test_direction_to_points_confondus() -> None:
    assert Vector3(5.0, 0.0, 5.0).direction_to(Vector3(5.0, 0.0, 5.0)) == Vector3()


def test_moved_towards_ne_depasse_pas_la_cible() -> None:
    start = Vector3()
    target = Vector3(0.0, 0.0, 10.0)
    assert start.moved_towards(target, 50.0) == Vector3(0.0, 0.0, 10.0)
    assert start.moved_towards(target, 4.0).z == pytest.approx(4.0)


def test_centroid_ensemble_vide() -> None:
    assert centroid([]) == Vector3()


def test_centroid_moyenne() -> None:
    result = centroid([Vector3(0.0, 0.0, 0.0), Vector3(10.0, 0.0, 20.0)])
    assert result.x == pytest.approx(5.0)
    assert result.z == pytest.approx(10.0)


def test_angle_between_vecteur_nul() -> None:
    assert angle_between(Vector3(), Vector3(1.0, 0.0, 0.0)) == 0.0


def test_is_in_rear_arc_detecte_le_dos() -> None:
    position = Vector3()
    heading = 0.0  # regarde vers +z
    assert is_in_rear_arc(position, heading, Vector3(0.0, 0.0, -10.0), 100.0)
    assert not is_in_rear_arc(position, heading, Vector3(0.0, 0.0, 10.0), 100.0)


def test_spread_positions_centree_et_espacee() -> None:
    positions = spread_positions(Vector3(), Vector3(1.0, 0.0, 0.0), 3, 10.0)
    assert [round(position.x, 3) for position in positions] == [-10.0, 0.0, 10.0]


def test_spread_positions_compte_nul() -> None:
    assert spread_positions(Vector3(), Vector3(1.0, 0.0, 0.0), 0, 10.0) == []


def test_heading_to() -> None:
    assert Vector3().heading_to(Vector3(0.0, 0.0, 10.0)) == pytest.approx(0.0)
    assert Vector3().heading_to(Vector3(10.0, 0.0, 0.0)) == pytest.approx(math.pi / 2)


def test_vector_from_dict_accepte_liste_et_mapping() -> None:
    assert Vector3.from_dict({"x": 1.0, "z": 2.0}) == Vector3(1.0, 0.0, 2.0)
    assert Vector3.from_dict([1.0, 2.0, 3.0]) == Vector3(1.0, 2.0, 3.0)


def test_vector_from_dict_rejette_les_donnees_invalides() -> None:
    with pytest.raises(SchemaError):
        Vector3.from_dict("12,0,4")
    with pytest.raises(SchemaError):
        Vector3.from_dict([1.0, 2.0])

"""Classification des unites du jeu vers la taxonomie interne."""

from __future__ import annotations

import pytest

from totalwar_ai.agent.unit_classifier import ClassificationRule, UnitClassifier
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import SchemaError
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

CONFIG = {
    "default_role": "unknown",
    "rules": [
        {"role": "lord", "when": {"tags_any": ["lord"]}},
        {"role": "hero_caster", "when": {"tags_any": ["hero"], "tags_all": ["caster"]}},
        {"role": "hero_melee", "when": {"tags_any": ["hero"]}},
        {"role": "artillery", "when": {"tags_any": ["artillery"]}},
        {"role": "shock_cavalry", "when": {"tags_any": ["cavalry"], "tags_all": ["shock"]}},
        {"role": "light_cavalry", "when": {"tags_any": ["cavalry"]}},
        {"role": "ranged_infantry", "when": {"key_contains": ["archer", "crossbow"]}},
        {"role": "melee_infantry", "when": {"tags_any": ["melee"], "tags_none": ["missile"]}},
    ],
}


@pytest.fixture
def classifier() -> UnitClassifier:
    return UnitClassifier.from_config(CONFIG)


def test_priorite_des_regles(classifier: UnitClassifier, make_unit) -> None:  # type: ignore[no-untyped-def]
    caster = make_unit("h1", Side.ALLY, UnitRole.UNKNOWN, tags=("hero", "caster"))
    fighter = make_unit("h2", Side.ALLY, UnitRole.UNKNOWN, tags=("hero",))
    assert classifier.classify(caster) is UnitRole.HERO_CASTER
    assert classifier.classify(fighter) is UnitRole.HERO_MELEE


def test_cavalerie_de_choc_avant_cavalerie_legere(classifier: UnitClassifier, make_unit) -> None:  # type: ignore[no-untyped-def]
    shock = make_unit("c1", Side.ALLY, UnitRole.UNKNOWN, tags=("cavalry", "shock"))
    light = make_unit("c2", Side.ALLY, UnitRole.UNKNOWN, tags=("cavalry",))
    assert classifier.classify(shock) is UnitRole.SHOCK_CAVALRY
    assert classifier.classify(light) is UnitRole.LIGHT_CAVALRY


def test_repli_sur_identifiant(classifier: UnitClassifier, make_unit) -> None:  # type: ignore[no-untyped-def]
    unit = make_unit("u1", Side.ALLY, UnitRole.UNKNOWN, unit_key="emp_crossbowmen")
    assert classifier.classify(unit) is UnitRole.RANGED_INFANTRY


def test_tags_none_exclut(classifier: UnitClassifier, make_unit) -> None:  # type: ignore[no-untyped-def]
    unit = make_unit("u2", Side.ALLY, UnitRole.UNKNOWN, tags=("melee", "missile"))
    assert classifier.classify(unit) is UnitRole.UNKNOWN


def test_role_deja_connu_est_conserve(classifier: UnitClassifier, make_unit) -> None:  # type: ignore[no-untyped-def]
    unit = make_unit("u3", Side.ALLY, UnitRole.ARTILLERY, tags=("cavalry",))
    assert classifier.classify(unit) is UnitRole.ARTILLERY


def test_classify_state_complete_les_roles(
    classifier: UnitClassifier, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    state = make_battle(
        [
            make_unit("a1", Side.ALLY, UnitRole.UNKNOWN, tags=("lord",)),
            make_unit("a2", Side.ALLY, UnitRole.MELEE_INFANTRY),
        ]
    )
    enriched = classifier.classify_state(state)
    assert enriched.unit("a1") is not None
    assert enriched.unit("a1").role is UnitRole.LORD  # type: ignore[union-attr]
    assert enriched.unit("a2").role is UnitRole.MELEE_INFANTRY  # type: ignore[union-attr]


def test_state_inchange_si_rien_a_classer(
    classifier: UnitClassifier, make_unit, make_battle
) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    assert classifier.classify_state(state) is state


def test_configuration_reelle_du_depot(make_unit) -> None:  # type: ignore[no-untyped-def]
    """Le YAML livre doit classer les cas usuels sans role explicite."""
    real = UnitClassifier.from_config()
    cases = {
        "empire_spearmen": UnitRole.SPEAR_INFANTRY,
        "great_cannon": UnitRole.ARTILLERY,
        "reiksguard_knight": UnitRole.SHOCK_CAVALRY,
        "outriders": UnitRole.LIGHT_CAVALRY,
    }
    for key, expected in cases.items():
        unit = make_unit(key, Side.ALLY, UnitRole.UNKNOWN, unit_key=key)
        assert real.classify(unit) is expected, key


def test_regle_invalide_rejetee() -> None:
    with pytest.raises(SchemaError, match="Role inconnu"):
        ClassificationRule.from_dict({"role": "dragon", "when": {"tags_any": ["big"]}})
    with pytest.raises(SchemaError, match="doit declarer un 'role'"):
        ClassificationRule.from_dict({"when": {"tags_any": ["big"]}})


def test_regle_sans_matcheur_ne_correspond_jamais() -> None:
    rule = ClassificationRule(role=UnitRole.LORD)
    assert not rule.matches(frozenset({"lord"}), "lord")


# --- cles reelles de WARHAMMER III -------------------------------------------


@pytest.mark.parametrize(
    ("unit_key", "attendu"),
    [
        # Relevees en bataille reelle.
        ("wh3_dlc20_chs_cha_daemon_prince_mnur", UnitRole.LORD),
        ("wh3_main_nur_inf_plaguebearers_1", UnitRole.MELEE_INFANTRY),
        # Gabarit du jeu, familles principales.
        ("wh_main_emp_art_great_cannon", UnitRole.ARTILLERY),
        ("wh_main_emp_cav_reiksguard", UnitRole.SHOCK_CAVALRY),
        ("wh_main_brt_mon_hippogryph", UnitRole.MONSTER),
        ("wh_main_emp_veh_steam_tank", UnitRole.CHARIOT),
        # Le nom l'emporte sur le segment : ces deux-la sont des `_inf_`.
        ("wh_main_emp_inf_handgunners", UnitRole.RANGED_INFANTRY),
        ("wh_main_emp_inf_spearmen", UnitRole.SPEAR_INFANTRY),
        # Le nom l'emporte aussi sur `_cav_`.
        ("wh_main_emp_cav_outriders", UnitRole.LIGHT_CAVALRY),
    ],
)
def test_les_cles_du_jeu_sont_classees(unit_key: str, attendu: UnitRole) -> None:
    """Aucune unite du jeu n'est codee en dur : tout passe par les regles YAML."""
    classifier = UnitClassifier.from_config()
    unite = UnitState(
        id="1001",
        side=Side.ALLY,
        unit_key=unit_key,
        position=Vector3(0.0, 0.0, 0.0),
    )
    assert classifier.classify(unite) is attendu


def test_une_cle_inconnue_reste_inconnue() -> None:
    """Mieux vaut `unknown` qu'une classification inventee."""
    classifier = UnitClassifier.from_config()
    unite = UnitState(
        id="1",
        side=Side.ALLY,
        unit_key="quelque_chose_de_totalement_autre",
        position=Vector3(0.0, 0.0, 0.0),
    )
    assert classifier.classify(unite) is UnitRole.UNKNOWN

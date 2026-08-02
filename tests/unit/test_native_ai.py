"""Doublure de l'IA du moteur, et mesure de la supervision.

Ce qui est verifie ici n'est pas que la doublure joue bien — elle ne le
pretend pas — mais qu'elle joue **vraiment** : sans cela, comparer une bataille
supervisee a une bataille de reference ne mesurerait rien du tout.
"""

from __future__ import annotations

from totalwar_ai.bridge.supervision import Supervisor
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.simulation.environment import (
    OrderKind,
    SimulationEnvironment,
    UnitSpec,
)
from totalwar_ai.simulation.native_ai import scripted_order
from totalwar_ai.simulation.runner import run_supervised_battle
from totalwar_ai.simulation.scenarios import ScenarioCatalog


def _environnement(*specs: UnitSpec, autopilot: bool = True) -> SimulationEnvironment:
    return SimulationEnvironment("t", specs, seed=1, ally_autopilot=autopilot)


def _unite(unit_id: str, role: UnitRole, side: Side, x: float = 0.0, z: float = 0.0) -> UnitSpec:
    return UnitSpec(id=unit_id, side=side, role=role, position=Vector3(x, 0.0, z))


# --- la politique elle-meme ----------------------------------------------------


def test_une_unite_de_melee_va_au_contact() -> None:
    env = _environnement(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
    )
    ordre = scripted_order(env.units["a"], [env.units["e"]])

    assert ordre is not None
    assert ordre.kind is OrderKind.ATTACK
    assert ordre.target_id == "e"


def test_une_unite_qui_attend_ne_s_avance_pas() -> None:
    """L'escarmouche : on tire sur ce qui vient, on ne va pas chercher."""
    env = _environnement(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
    )
    ordre = scripted_order(env.units["a"], [env.units["e"]], waits=True)

    assert ordre is not None and ordre.kind is OrderKind.HOLD


def test_la_cavalerie_prefere_les_tireurs_au_plus_proche() -> None:
    """Angle mort connu du moteur, et raison d'etre de la regle `tireur_au_contact`."""
    env = _environnement(
        _unite("cav", UnitRole.SHOCK_CAVALRY, Side.ALLY),
        _unite("proche", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=40.0),
        _unite("archers", UnitRole.RANGED_INFANTRY, Side.ENEMY, z=160.0),
    )
    ordre = scripted_order(env.units["cav"], [env.units["proche"], env.units["archers"]])

    assert ordre is not None
    assert ordre.target_id == "archers", "la cavalerie a pris le plus proche"


def test_une_unite_deja_au_contact_est_laissee_tranquille() -> None:
    """La reordonner en pleine melee la ferait decrocher pour rien."""
    env = _environnement(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=5.0),
    )
    env.units["a"].engaged_with = {"e"}

    assert scripted_order(env.units["a"], [env.units["e"]]) is None


# --- la doublure menant nos unites ---------------------------------------------


def test_sans_pilote_automatique_notre_armee_ne_recoit_rien() -> None:
    """Le simulateur sert d'abord a mesurer notre agent : il garde la main."""
    env = _environnement(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
        autopilot=False,
    )
    env.step()

    assert env.units["a"].order.kind is OrderKind.HOLD


def test_avec_pilote_automatique_notre_armee_engage() -> None:
    env = _environnement(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
    )
    env.step()

    assert env.units["a"].order.kind is OrderKind.ATTACK


def test_une_unite_reprise_echappe_au_pilote_automatique() -> None:
    """Sans cela l'ordre du superviseur serait ecrase au tick suivant.

    C'est toute la mecanique de la supervision : reprendre une unite, lui
    donner un ordre, et que cet ordre tienne.
    """
    env = _environnement(
        _unite("a", UnitRole.MELEE_INFANTRY, Side.ALLY),
        _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY, z=200.0),
    )
    env.manual.add("a")
    env.step()

    assert env.units["a"].order.kind is not OrderKind.ATTACK


# --- la mesure de bout en bout -------------------------------------------------


def test_la_doublure_mene_une_vraie_bataille() -> None:
    """Une doublure qui produirait des matchs nuls vides ne mesurerait rien."""
    resultat = run_supervised_battle(ScenarioCatalog().get("balanced_clash"), seed=11)

    assert resultat.outcome is not BattleOutcomeKind.UNKNOWN
    assert resultat.summary.duration > 0.0
    assert resultat.summary.enemy_remaining < 1.0, "l'adversaire n'a pas perdu un homme"
    assert resultat.interventions == (), "aucune regle n'etait active"


def test_la_reference_est_reproductible() -> None:
    """Deux mesures a graine egale doivent coincider, sans quoi rien n'est comparable."""
    scenario = ScenarioCatalog().get("balanced_clash")
    premiere = run_supervised_battle(scenario, seed=23)
    seconde = run_supervised_battle(scenario, seed=23)

    assert premiere.outcome is seconde.outcome
    assert premiere.summary.ally_remaining == seconde.summary.ally_remaining


def test_le_superviseur_intervient_et_c_est_trace() -> None:
    """Le motif de chaque reprise est retenu : une mesure muette n'explique rien."""
    resultat = run_supervised_battle(
        ScenarioCatalog().get("skirmish_standoff"), seed=11, supervisor=Supervisor()
    )

    assert resultat.interventions, "le superviseur n'a rien repris"
    assert all(isinstance(motif, str) and motif for motif in resultat.interventions)
    assert resultat.summary.metrics["interventions"] == len(resultat.interventions)

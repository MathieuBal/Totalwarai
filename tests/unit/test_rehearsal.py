"""Ce que nos regles auraient fait d'une bataille deja jouee.

Une regle se juge d'abord sur une question que le banc ne pose pas : se
declenche-t-elle en vraie bataille, et **avant** que tout soit joue ?
"""

from __future__ import annotations

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState
from totalwar_ai.learning.rehearsal import rehearse, render_cascade, rout_cascade


def _unite(
    unit_id: str,
    role: UnitRole,
    side: Side = Side.ALLY,
    *,
    melee: bool = False,
    routing: bool = False,
    hp: float = 1.0,
    ammo: float = 0.0,
) -> UnitState:
    return UnitState(
        id=unit_id,
        side=side,
        role=role,
        position=Vector3(0.0, 0.0, 0.0 if side is Side.ALLY else 100.0),
        is_engaged=melee,
        is_routing=routing,
        health_ratio=hp,
        entity_ratio=hp,
        ammo_ratio=ammo,
    )


def _etats(*tours: tuple[UnitState, ...]) -> list[BattleState]:
    return [
        BattleState(battle_id="r", game_time=index * 10.0, units=unites)
        for index, unites in enumerate(tours)
    ]


# --- le passage a blanc ---------------------------------------------------------


def test_une_regle_qui_a_matiere_a_agir_est_comptee() -> None:
    etats = _etats(
        (
            _unite("arc", UnitRole.RANGED_INFANTRY, ammo=0.5),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
        (
            _unite("arc", UnitRole.RANGED_INFANTRY, melee=True, ammo=0.5),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
    )

    resultat = rehearse(etats)
    tireur = next(item for item in resultat.firings if item.rule == "tireur_au_contact")
    assert tireur.states == 1
    assert tireur.first == 10.0
    assert tireur.first_unit == "arc"


def test_une_regle_sans_matiere_le_dit() -> None:
    """L'essai n° 11 a tourne sans une seule intervention, et ne disait rien."""
    etats = _etats(
        (_unite("inf", UnitRole.MELEE_INFANTRY), _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY)),
        (_unite("inf", UnitRole.MELEE_INFANTRY), _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY)),
    )

    resultat = rehearse(etats)
    assert resultat.triggered == []
    assert "jamais" in resultat.render()


def test_une_regle_qui_se_declenche_apres_la_deroute_arrive_trop_tard() -> None:
    """C'est la seule chose que ce module tranche, et elle ne demande aucune theorie."""
    etats = _etats(
        (_unite("inf", UnitRole.MELEE_INFANTRY), _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY)),
        (
            _unite("inf", UnitRole.MELEE_INFANTRY, routing=True),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
        (
            _unite("inf", UnitRole.MELEE_INFANTRY, routing=True),
            _unite("arc", UnitRole.RANGED_INFANTRY, melee=True, ammo=0.5),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
    )

    rendu = rehearse(etats).render()
    assert "Premiere deroute alliee : t=10s" in rendu
    assert "trop tard" in rendu


def test_une_regle_qui_previent_la_deroute_le_dit() -> None:
    etats = _etats(
        (
            _unite("arc", UnitRole.RANGED_INFANTRY, melee=True, ammo=0.5),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
        (
            _unite("arc", UnitRole.RANGED_INFANTRY, melee=True, ammo=0.5),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
        (
            _unite("arc", UnitRole.RANGED_INFANTRY, routing=True, ammo=0.5),
            _unite("e", UnitRole.MELEE_INFANTRY, Side.ENEMY),
        ),
    )

    rendu = rehearse(etats).render()
    assert "se declenche 20 s avant" in rendu


# --- la cascade de deroute ------------------------------------------------------


def test_la_cascade_donne_la_sante_d_avant_la_rupture() -> None:
    """Au moment ou elle rompt, l'unite a deja commence a se faire tailler."""
    etats = _etats(
        (_unite("a", UnitRole.MELEE_INFANTRY, hp=0.6),),
        (_unite("a", UnitRole.MELEE_INFANTRY, hp=0.5),),
        (_unite("a", UnitRole.MELEE_INFANTRY, hp=0.2, routing=True),),
    )

    cascade = rout_cascade(etats)
    assert cascade == [(20.0, "a", 0.5)]


def test_une_rupture_a_haute_sante_est_signalee_comme_contagion() -> None:
    """Ce n'est pas de l'usure : six unites ont rompu au-dessus de 39 %."""
    etats = _etats(
        (
            _unite("a", UnitRole.MELEE_INFANTRY, hp=0.7),
            _unite("b", UnitRole.MELEE_INFANTRY, hp=0.15),
        ),
        (
            _unite("a", UnitRole.MELEE_INFANTRY, hp=0.7, routing=True),
            _unite("b", UnitRole.MELEE_INFANTRY, hp=0.15, routing=True),
        ),
    )

    rendu = render_cascade(rout_cascade(etats))
    assert "2 unite(s) alliee(s) ont rompu" in rendu
    assert "1 ont rompu au-dessus de 40 %" in rendu
    assert "contagion" in rendu


def test_une_armee_qui_tient_ne_produit_aucune_cascade() -> None:
    etats = _etats(
        (_unite("a", UnitRole.MELEE_INFANTRY),),
        (_unite("a", UnitRole.MELEE_INFANTRY),),
    )
    assert rout_cascade(etats) == []
    assert "Aucune unite alliee n'a rompu" in render_cascade(rout_cascade(etats))

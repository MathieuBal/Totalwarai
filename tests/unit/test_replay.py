"""Relire une bataille enregistree comme si la sonde parlait encore.

Le corpus d'apprentissage est ecrit dans un format compact — inventaire d'un
cote, positions de l'autre, booleens seulement quand ils sont vrais. Si la
relecture ne rend pas exactement ce que la boucle avait sous les yeux,
l'apprentissage porte sur des batailles qui n'ont pas eu lieu.
"""

from __future__ import annotations

from pathlib import Path

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.live import LiveStep
from totalwar_ai.bridge.recording import BattleRecorder
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.learning.replay import read_states


def _unite(
    unit_id: str,
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    type_unite: str = "wh_main_emp_inf_spearmen",
    alive: bool = True,
    melee: bool = False,
    ammo: int | None = None,
    portee: float | None = None,
    hommes: int | None = None,
) -> ProbeUnitObservation:
    return ProbeUnitObservation(
        unit_id=unit_id,
        position=Vector3(x, y, z),
        unit_type=type_unite,
        alive=alive,
        in_melee=melee,
        ammo=ammo,
        missile_range=portee,
        men_alive=hommes,
    )


def _ecrit(tmp_path: Path, etats: list[ProbeBattleState]) -> Path:
    recorder = BattleRecorder(directory=tmp_path)
    for state in etats:
        recorder.observe(LiveStep(state=state))
    recorder.close()
    assert recorder.path is not None
    return recorder.path


def _etat(
    allies: tuple[ProbeUnitObservation, ...],
    enemies: tuple[ProbeUnitObservation, ...],
    *,
    ms: int = 0,
    sequence: int = 0,
) -> ProbeBattleState:
    return ProbeBattleState(
        allies=allies, enemies=enemies, game_time_ms=ms, sequence=sequence, phase="Deployed"
    )


# --- ce que la relecture rend --------------------------------------------------


def test_les_positions_et_les_camps_survivent_a_l_aller_retour(tmp_path: Path) -> None:
    chemin = _ecrit(
        tmp_path,
        [
            _etat((_unite("a", x=10.0, y=21.5, z=-30.0),), (_unite("e", x=40.0, z=60.0),), ms=1000),
            _etat((_unite("a", x=15.0, y=22.0, z=-20.0),), (_unite("e", x=40.0, z=60.0),), ms=1500),
        ],
    )

    etats = read_states(chemin)
    assert len(etats) == 2
    premier = etats[0]
    allie = next(unit for unit in premier.units if unit.id == "a")
    assert allie.side is Side.ALLY
    assert (allie.position.x, allie.position.z) == (10.0, -30.0)
    # L'altitude est la seule donnee de terrain que le jeu donne : la perdre
    # a la relecture reviendrait a ne l'avoir jamais enregistree.
    assert allie.position.y == 21.5
    assert next(unit for unit in premier.units if unit.id == "e").side is Side.ENEMY
    assert premier.game_time == 1.0


def test_les_roles_sont_deduits_comme_en_direct(tmp_path: Path) -> None:
    """Le jeu ne dit pas qu'une unite est de l'artillerie : le classifieur, si."""
    chemin = _ecrit(
        tmp_path,
        [
            _etat(
                (_unite("canon", type_unite="wh_main_emp_art_great_cannon"),),
                (_unite("lanciers", type_unite="wh_main_emp_inf_spearmen"),),
            ),
        ],
    )

    etats = read_states(chemin)
    roles = {unit.id: unit.role for unit in etats[0].units}
    assert roles["canon"] is UnitRole.ARTILLERY
    assert roles["lanciers"] is not UnitRole.UNKNOWN


def test_les_munitions_se_recalculent_comme_en_bataille(tmp_path: Path) -> None:
    """Le jeu donne un total, pas un rapport : le maximum vu est la dotation.

    C'est exactement ce que fait `RosterMemory` en direct. Le refaire autrement
    ici ferait mentir toute comparaison entre une bataille rejouee et la meme
    bataille vue en direct.
    """
    chemin = _ecrit(
        tmp_path,
        [
            _etat((_unite("arc", ammo=480, portee=120.0),), (_unite("e", z=100.0),)),
            _etat((_unite("arc", ammo=240, portee=120.0),), (_unite("e", z=100.0),), ms=500),
        ],
    )

    etats = read_states(chemin)
    munitions = [next(u for u in etat.units if u.id == "arc").ammo_ratio for etat in etats]
    assert munitions == [1.0, 0.5]


def test_les_morts_ne_reviennent_pas(tmp_path: Path) -> None:
    """`to_battle_state` ecarte les unites mortes : la relecture aussi."""
    chemin = _ecrit(
        tmp_path,
        [
            _etat((_unite("a"),), (_unite("e1"), _unite("e2", alive=False)), ms=1000),
        ],
    )

    etats = read_states(chemin)
    assert {unit.id for unit in etats[0].units} == {"a", "e1"}


def test_le_contact_est_relu(tmp_path: Path) -> None:
    """Sans lui, l'inference ne distinguerait plus une melee d'une approche."""
    chemin = _ecrit(tmp_path, [_etat((_unite("a", melee=True),), (_unite("e", melee=True),))])

    etats = read_states(chemin)
    assert all(unit.is_engaged for unit in etats[0].units)


# --- ce que la relecture refuse -------------------------------------------------


def test_un_fichier_absent_ne_leve_pas(tmp_path: Path) -> None:
    """Un corpus de trente batailles ne doit pas tomber sur la premiere manquante."""
    assert read_states(tmp_path / "jamais_ecrit.jsonl") == []


def test_une_ligne_tronquee_n_emporte_pas_la_bataille(tmp_path: Path) -> None:
    """Le jeu peut planter en pleine ecriture. Le reste de la bataille tient."""
    chemin = _ecrit(
        tmp_path,
        [
            _etat((_unite("a"),), (_unite("e"),), ms=1000),
            _etat((_unite("a", x=5.0),), (_unite("e"),), ms=1500),
        ],
    )
    with chemin.open("a", encoding="utf-8") as handle:
        handle.write('{"turn": 3, "units": [{"id"\n')

    assert len(read_states(chemin)) == 2


def test_une_unite_hors_inventaire_n_est_pas_rangee_d_office(tmp_path: Path) -> None:
    """Sans camp connu, la ranger avec nous en ferait une alliee imaginaire."""
    chemin = _ecrit(tmp_path, [_etat((_unite("a"),), (_unite("e"),), ms=1000)])
    lignes = [
        ligne
        for ligne in chemin.read_text(encoding="utf-8").splitlines()
        if '"roster"' not in ligne
    ]
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")

    assert read_states(chemin)[0].units == ()


# --- la chaine complete ---------------------------------------------------------


def test_une_bataille_relue_se_laisse_inferer(tmp_path: Path) -> None:
    """Relecture, inference, apprentissage : la chaine tient bout a bout."""
    from totalwar_ai.learning.observation import Move, infer

    etats = []
    for tour in range(6):
        etats.append(
            _etat(
                (
                    _unite(
                        "cav", x=0.0, z=-100.0 + tour * 15.0, type_unite="wh_main_emp_cav_knights"
                    ),
                ),
                (
                    _unite("arc", x=0.0, z=0.0, type_unite="wh_main_emp_inf_handgunners"),
                    _unite("inf", x=300.0, z=0.0, type_unite="wh_main_emp_inf_spearmen"),
                ),
                ms=tour * 500,
                sequence=tour,
            )
        )
    chemin = _ecrit(tmp_path, etats)

    resultat = infer(read_states(chemin))
    approches = [item for item in resultat.observations if item.move is Move.CLOSE]
    assert approches, "aucune approche inferee sur une bataille relue"
    assert approches[0].target_id == "arc"
    # Ce qui etait offert au choix doit survivre a la relecture : sans cela,
    # l'apprentissage du ciblage ne pourrait rien normaliser.
    assert set(approches[0].available) == {UnitRole.RANGED_INFANTRY, UnitRole.SPEAR_INFANTRY}


def test_une_cible_refusee_par_le_jeu_survit_a_l_enregistrement(tmp_path: Path) -> None:
    """**Le drapeau existait en direct et se perdait a l'ecriture.**

    `targetable` vient de `is_valid_target()` : il ne dit pas si l'unite est
    vivante, il dit si le jeu acceptera un ordre d'attaque. Trois ennemis ont ete
    declares non ciblables 665, 644 et 644 fois sur deux batailles reelles.
    `Planner.can_be_attacked` s'appuie dessus en direct — un corpus qui l'ignore
    apprend d'une bataille ou tout etait attaquable, ce qui n'a jamais ete vrai.
    """
    interdite = ProbeUnitObservation(
        unit_id="e1",
        position=Vector3(10.0, 0.0, 20.0),
        unit_type="wh_dlc_grn_inf_archers",
        men_alive=60,
        targetable=False,
    )
    ordinaire = ProbeUnitObservation(
        unit_id="e2",
        position=Vector3(30.0, 0.0, 20.0),
        unit_type="wh_dlc_grn_inf_archers",
        men_alive=60,
    )
    nous = ProbeUnitObservation(
        unit_id="a1",
        position=Vector3(0.0, 0.0, 0.0),
        unit_type="wh_main_emp_inf_swordsmen",
        controllable=True,
        men_alive=80,
    )
    etat = ProbeBattleState(
        allies=(nous,), enemies=(interdite, ordinaire), phase="Deployed", sequence=1
    )

    recorder = BattleRecorder(directory=tmp_path)
    recorder.observe(LiveStep(state=etat))
    recorder.close()
    assert recorder.path is not None

    rejoue = read_states(recorder.path)
    assert rejoue, "l'enregistrement n'a rien rendu"
    par_id = {unite.id: unite for unite in rejoue[-1].enemies(available_only=False)}

    assert par_id["e1"].metadata["targetable"] is False, "le refus du jeu s'est perdu"
    assert par_id["e2"].metadata["targetable"] is True

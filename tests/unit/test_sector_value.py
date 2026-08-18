"""Ce qu'un secteur vaut : la mesure, et ce qu'elle ne doit pas contaminer.

La sonde impose un secteur pour obtenir la ligne de comparaison qui n'existe
nulle part — `best()` n'en choisit qu'un par etat. Ces tests pincent surtout
qu'elle ne fuit pas en production, parce qu'un canal de mesure qui influence la
decision fabrique exactement le genre de chiffre flatteur que cette session a
passe son temps a demasquer.
"""

from __future__ import annotations

from totalwar_ai.agent.planner import Planner
from totalwar_ai.agent.sectors import commit, split_sectors
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.learning.evaluation import DEFAULT_SEEDS, widen_seeds
from totalwar_ai.learning.sector_value import (
    SectorRecord,
    Study,
    describe_sector,
)

FRONT = Vector3(0.0, 0.0, 1.0)


def _record(**overrides) -> SectorRecord:  # type: ignore[no-untyped-def]
    champs = {
        "scenario": "essai",
        "seed": 11,
        "sector": 0,
        "started_at": 0.0,
        "ratio": 2.0,
        "enemy_share": 0.5,
        "isolated": 0,
        "enemies": 2,
        "high_value": False,
        "ranged": False,
        "distance_to_mass": 10.0,
        "support": 0.0,
        "attackers": 2,
        "line_committed": 0.4,
        "broke": True,
        "broke_at": 20.0,
        "exchange": 1.0,
        "followup": 1.2,
        "outcome": "victory",
    }
    champs.update(overrides)
    return SectorRecord(**champs)  # type: ignore[arg-type]


# --- la mesure ne doit pas fuir ----------------------------------------------


def test_sans_secteur_impose_le_planificateur_est_inchange() -> None:
    """Le canal de mesure est eteint par defaut, et le reste.

    C'est la garantie qui compte : un `forced_sector` actif par megarde
    changerait le banc sans que rien ne le dise.
    """
    planificateur = Planner()
    assert planificateur.forced_sector is None
    assert planificateur.sliding_window is False


def test_la_fenetre_glissante_est_eteinte_par_defaut(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Sans `slide`, la composition est celle d'avant, veto compris.

    La fenetre glissante compose des assauts que la production refuse — mesure a
    l'ADR 0019 : `skirmish_standoff` y passe du nul a la defaite. Elle sert la
    mesure, jamais la decision.
    """
    ennemis = [make_unit("e0", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 200.0)]
    # Une rapide loin devant, trois lentes groupees : le veto de la plus rapide.
    allies = [
        make_unit("cav", Side.ALLY, UnitRole.SHOCK_CAVALRY, 0.0, 120.0),
        *(
            make_unit(f"inf{index}", Side.ALLY, UnitRole.MELEE_INFANTRY, float(index * 5), 0.0)
            for index in range(3)
        ),
    ]
    etat = make_battle([*allies, *ennemis])
    carte = split_sectors(etat, FRONT, allies)
    secteur = carte.sectors[0]

    sans = commit(secteur, etat, allies, game_time=0.0)
    avec = commit(secteur, etat, allies, game_time=0.0, slide=True)
    assert sans is None, "la production doit continuer de refuser"
    assert avec is not None, "la mesure doit pouvoir composer"
    assert len(avec.attackers) >= 2


# --- les attributs mesures ----------------------------------------------------


def test_un_ennemi_sans_voisin_est_compte_comme_isole(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """La configuration exacte du secteur a 4,92 de `skirmish_standoff`."""
    isole = make_unit("e_seul", Side.ENEMY, UnitRole.MELEE_INFANTRY, 300.0, 300.0)
    groupe = [
        make_unit(f"e{index}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(index * 10), 0.0)
        for index in range(3)
    ]
    allies = [make_unit("a0", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, -100.0)]
    etat = make_battle([*allies, isole, *groupe])

    seul = describe_sector(etat, "essai", 11, 0, 0.0, 2.0, ["e_seul"], ["a0"])
    assert seul["isolated"] == 1
    assert seul["support"] == 0.0, "personne ne peut lui venir en aide"

    dense = describe_sector(etat, "essai", 11, 1, 0.0, 2.0, ["e0"], ["a0"])
    assert dense["isolated"] == 0
    assert dense["support"] > 0.0, "ses voisins peuvent se retourner"


def test_la_part_de_ligne_engagee_se_mesure_sur_la_melee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Envoyer la moitie de sa ligne n'est pas la meme chose qu'en envoyer un dixieme.

    Les tireurs n'y entrent pas : ils ne vont pas au contact, et les compter
    ferait paraitre l'engagement plus leger qu'il n'est — c'est la meme erreur
    de numerateur que l'ADR 0018 a corrigee.
    """
    ennemis = [make_unit("e0", Side.ENEMY, UnitRole.MELEE_INFANTRY, 0.0, 100.0)]
    allies = [
        make_unit("inf0", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0),
        make_unit("inf1", Side.ALLY, UnitRole.MELEE_INFANTRY, 10.0, 0.0),
        make_unit("arc0", Side.ALLY, UnitRole.RANGED_INFANTRY, 20.0, 0.0),
    ]
    etat = make_battle([*allies, *ennemis])
    attributs = describe_sector(etat, "essai", 11, 0, 0.0, 2.0, ["e0"], ["inf0"])
    assert attributs["line_committed"] == 0.5, "une des deux unites de melee"


def test_la_part_de_l_armee_adverse_est_relative(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    ennemis = [
        make_unit(f"e{index}", Side.ENEMY, UnitRole.MELEE_INFANTRY, float(index * 10), 100.0)
        for index in range(4)
    ]
    allies = [make_unit("a0", Side.ALLY, UnitRole.MELEE_INFANTRY, 0.0, 0.0)]
    etat = make_battle([*allies, *ennemis])
    attributs = describe_sector(etat, "essai", 11, 0, 0.0, 2.0, ["e0"], ["a0"])
    assert attributs["enemy_share"] == 0.25


# --- la lecture ---------------------------------------------------------------


def test_rejouer_la_meme_configuration_ne_fait_pas_plusieurs_mesures() -> None:
    """Le defaut que ce module a produit a son premier passage.

    96 releves paraissaient confortables ; ils ne portaient que **quatre formes
    de secteur**, une par scenario, repetees sur douze graines et trois indices.
    Le seuil porte donc sur les configurations distinctes, jamais sur le nombre
    de releves.
    """
    etude = Study(records=[_record(seed=graine) for graine in range(50)])
    assert len(etude.records) == 50
    assert len(etude.configurations) == 1
    assert not etude.measured, "cinquante rejeux d'une meme forme ne sont pas une mesure"
    assert "on ne conclut" in etude.render()


def test_un_attribut_qui_ne_fait_que_renommer_le_scenario_est_signale() -> None:
    """« Secteur valant moins d'un quart de leur armee » separait parfaitement
    les bons des mauvais echanges — et ne selectionnait rien d'autre que
    `skirmish_standoff`.

    Sans ce garde-fou, la lecture publiait une correlation qui n'en etait pas
    une : rien ne permet de dire si c'est l'attribut ou la bataille qui explique
    l'issue.
    """
    etude = Study(
        records=[
            *(_record(scenario="standoff", enemy_share=0.12, exchange=-2.0) for _ in range(12)),
            *(_record(scenario="melee", enemy_share=0.50, exchange=+2.0) for _ in range(12)),
        ]
    )
    assert etude.confounded(lambda item: item.enemy_share < 0.25)
    assert "confondu avec le scenario" in etude.render()


def test_un_attribut_present_des_deux_cotes_n_est_pas_confondu() -> None:
    """Le garde-fou doit pouvoir dire non, sinon il ne dit rien."""
    etude = Study(
        records=[
            _record(scenario="melee", enemy_share=0.12),
            _record(scenario="melee", enemy_share=0.50),
            _record(scenario="standoff", enemy_share=0.12),
            _record(scenario="standoff", enemy_share=0.50),
        ]
    )
    assert not etude.confounded(lambda item: item.enemy_share < 0.25)


def test_une_etude_vide_le_dit_au_lieu_de_conclure() -> None:
    """Un instrument qui tranche sur une case vide est un instrument qui ment."""
    assert "Aucun assaut observe" in Study().render()


def test_la_lecture_separe_les_deux_cotes_de_chaque_attribut() -> None:
    """C'est la comparaison qui doit avoir un sens, pas le chiffre isole."""
    etude = Study(
        records=[
            *(_record(isolated=1, exchange=-2.0) for _ in range(5)),
            *(_record(isolated=0, exchange=+2.0) for _ in range(5)),
        ]
    )
    isoles, denses = etude.split(lambda item: item.isolated > 0)
    assert len(isoles) == 5 and len(denses) == 5
    rendu = etude.render()
    assert "-2.00" in rendu and "+2.00" in rendu


# --- les graines ---------------------------------------------------------------


def test_la_sonde_et_le_banc_tirent_les_memes_graines() -> None:
    """Deux regles d'elargissement finiraient par deriver l'une de l'autre.

    Sans graines communes, les chiffres de la sonde et ceux du banc ne se
    comparent pas — et c'est precisement la comparaison qui les rend utiles.
    """
    assert widen_seeds(3) == DEFAULT_SEEDS
    assert widen_seeds(12)[:3] == DEFAULT_SEEDS
    assert len(widen_seeds(12)) == 12
    # Aucun pool reserve n'est atteint : ils commencent a 9001.
    assert all(graine < 9000 for graine in widen_seeds(12))

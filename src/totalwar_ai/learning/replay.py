"""Relire une bataille enregistree comme si la sonde parlait encore.

**Pourquoi ceci existe.** L'enregistrement est ecrit pour tenir peu de place :
l'inventaire des unites d'un cote, ce qui bouge de l'autre, les booleens
seulement quand ils sont vrais. C'est un bon format de stockage et un mauvais
format de travail — l'inference et l'apprentissage veulent des
:class:`~totalwar_ai.domain.battle_state.BattleState`, comme le simulateur en
produit.

Ce module refait le chemin en sens inverse et rend exactement ce que la boucle
de pilotage avait sous les yeux, roles compris. Sans lui, le corpus de trente
batailles resterait des fichiers illisibles par tout le reste du projet.

.. rubric:: Deux precautions

**Les ratios se reconstituent comme en direct.** Le jeu ne donne ni l'effectif
nominal ni la dotation de munitions : `RosterMemory` les deduit du maximum
observe. On rejoue donc la meme deduction, sur les memes donnees — un ratio
calcule autrement ici qu'en bataille ferait mentir toute comparaison.

**Les roles sont deduits, jamais lus.** Le jeu ne dit pas qu'une unite est de
l'artillerie ; le classifieur le deduit de sa cle. C'est le meme classifieur
qu'en direct, pour la meme raison.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.roster import RosterMemory
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3


def read_states(path: Path) -> list[BattleState]:
    """Etats de bataille d'un enregistrement, dans l'ordre, roles deduits.

    Rend une liste vide plutot que de lever : un enregistrement tronque par un
    plantage du jeu ne doit pas condamner le corpus entier.
    """
    return list(iter_states(path))


def iter_states(path: Path) -> Iterator[BattleState]:
    """Comme :func:`read_states`, sans tout charger en memoire.

    Un corpus de trente batailles pese des dizaines de mega-octets ; les tenir
    toutes ouvertes en meme temps n'apporterait rien.
    """
    from totalwar_ai.agent.unit_classifier import UnitClassifier

    classifieur = UnitClassifier.from_config()
    inventaire: dict[str, dict[str, Any]] = {}
    memoire = RosterMemory()
    identifiant = Path(path).stem

    for ligne in _lines(path):
        if "roster" in ligne:
            inventaire.update(ligne["roster"] or {})
            continue
        if "units" not in ligne:
            # En-tete de format, ou tour enregistre sans les unites : il n'y a
            # rien a reconstituer.
            continue
        sonde = _probe_state(ligne, inventaire)
        # Meme ordre qu'en direct : on observe d'abord, on calcule ensuite. Le
        # maximum vu jusqu'ici est l'effectif de depart.
        memoire.observe(sonde)
        domaine = sonde.to_battle_state(
            identifiant,
            entity_ratios=_ratios(sonde, memoire.entity_ratio),
            ammo_ratios=_ratios(sonde, memoire.ammo_ratio),
        )
        yield classifieur.classify_state(domaine)


def _probe_state(ligne: dict[str, Any], inventaire: dict[str, dict[str, Any]]) -> ProbeBattleState:
    """Un tour enregistre, rendu sous la forme que la sonde publiait."""
    allies: list[ProbeUnitObservation] = []
    ennemis: list[ProbeUnitObservation] = []
    for brut in ligne.get("units") or []:
        fiche = inventaire.get(str(brut.get("id", "")), {})
        observation = _observation(brut, fiche)
        # Une unite absente de l'inventaire n'a pas de camp connu. La ranger
        # d'office avec nous en ferait une alliee imaginaire : on la laisse de
        # cote, et le compte des unites le dira.
        if fiche.get("side") == "ally":
            allies.append(observation)
        elif fiche.get("side") == "enemy":
            ennemis.append(observation)
    return ProbeBattleState(
        allies=tuple(allies),
        enemies=tuple(ennemis),
        sequence=int(ligne.get("sequence", 0) or 0),
        game_time_ms=int(ligne.get("game_time_ms", 0) or 0),
        phase=str(ligne.get("phase", "")),
    )


def _observation(brut: dict[str, Any], fiche: dict[str, Any]) -> ProbeUnitObservation:
    """Une unite, telle que la sonde l'avait vue.

    **Un champ absent reste `None`, jamais zero.** C'est la regle du protocole,
    et la relire autrement ferait apparaitre des moraux et des munitions que le
    jeu n'a jamais donnes.
    """
    return ProbeUnitObservation(
        unit_id=str(brut.get("id", "")),
        position=Vector3(
            float(brut.get("x", 0.0)),
            float(brut.get("y", 0.0)),
            float(brut.get("z", 0.0)),
        ),
        unit_type=str(fiche.get("type", "")),
        controllable=fiche.get("side") == "ally",
        commanding=bool(fiche.get("commanding", False)),
        idle=bool(brut.get("idle", False)),
        alive=not bool(brut.get("dead", False)),
        in_melee=bool(brut.get("in_melee", False)),
        routing=bool(brut.get("routing", False)),
        hidden=bool(brut.get("hidden", False)),
        can_fly=bool(fiche.get("can_fly", False)),
        hitpoints=_optional_float(brut.get("hitpoints")),
        men_alive=_optional_int(brut.get("men_alive")),
        bearing=_optional_float(brut.get("bearing")),
        ammo=_optional_int(brut.get("ammo")),
        missile_range=_optional_float(fiche.get("missile_range")),
    )


def _ratios(state: ProbeBattleState, mesure: Any) -> dict[str, float]:
    resultats: dict[str, float] = {}
    for observation in (*state.allies, *state.enemies):
        valeur = mesure(observation)
        if valeur is not None:
            resultats[observation.unit_id] = valeur
    return resultats


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _lines(path: Path) -> Iterator[dict[str, Any]]:
    """Lignes JSON d'un enregistrement, les illisibles etant sautees."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            for ligne in handle:
                ligne = ligne.strip()
                if not ligne:
                    continue
                try:
                    charge = json.loads(ligne)
                except json.JSONDecodeError:
                    continue
                if isinstance(charge, dict):
                    yield charge
    except OSError:
        # Un corpus de trente batailles ne doit pas tomber sur la premiere
        # manquante ou illisible.
        return

"""Ce que valent les batailles enregistrees, avant d'apprendre dessus.

**Pourquoi ceci existe.** Constituer un corpus demande de jouer des dizaines de
batailles, et c'est du temps qui ne se rattrape pas. Une bataille interrompue,
un flux troue, un enregistrement fait avant que les unites n'y figurent : rien
de tout cela ne se voit a l'oeil nu dans un fichier de deux mega-octets.

Ce module lit les enregistrements et dit, bataille par bataille, ce qu'ils
valent. Il n'ecarte rien de lui-meme — l'operateur a joue ces parties, elles lui
appartiennent — mais l'apprentissage saura lesquelles laisser de cote, et le
dira.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Completude en deca de laquelle une bataille n'est pas exploitable.
#:
#: Trois exigences valent chacune un tiers : les unites y figurent, le flux est
#: continu, la bataille est allee a son terme. Deux sur trois suffisent a
#: apprendre le ciblage ; il en faut trois pour rattacher une decision a une
#: issue.
MINIMUM_COMPLETENESS = 0.6

#: Batailles detaillees dans le tableau. Au-dela, on resume.
_MAX_LISTED = 25


@dataclass(frozen=True, slots=True)
class BattleHealth:
    """Fiche de sante d'une bataille enregistree."""

    path: Path
    battle_id: str
    observations: int = 0
    decisions: int = 0
    duration: float = 0.0
    units_seen: int = 0
    #: Etats que la sonde a publies et que nous n'avons pas enregistres.
    gaps: int = 0
    has_units: bool = False
    has_shadow: bool = False
    finished: bool = False
    error: str = ""

    @property
    def completeness(self) -> float:
        """Part des trois exigences satisfaites, dans [0, 1]."""
        if self.error:
            return 0.0
        continu = self.observations > 0 and self.gaps == 0
        return sum((self.has_units, continu, self.finished)) / 3.0

    @property
    def usable(self) -> bool:
        return self.completeness >= MINIMUM_COMPLETENESS

    def explain(self) -> str:
        """Une ligne, dans la langue de l'operateur."""
        if self.error:
            return f"{self.battle_id[:8]}  illisible : {self.error}"
        manques = []
        if not self.has_units:
            manques.append("sans les unites")
        if self.gaps:
            manques.append(f"{self.gaps} etat(s) manquant(s)")
        if not self.finished:
            manques.append("issue inconnue")
        etat = ", ".join(manques) if manques else "complete"
        # La decision fantome n'entre pas dans la completude : sans elle on
        # apprend encore ce que l'IA du moteur a fait, on perd seulement la
        # comparaison avec ce que nous aurions fait. C'est une mention, pas un
        # manque.
        if not self.has_shadow:
            etat += " (sans fantome)"
        return (
            f"{self.battle_id[:8]}  {self.observations:5d} etats  "
            f"{self.decisions:4d} decisions  {self.duration:6.0f}s  "
            f"{self.units_seen:3d} unites  {self.completeness:4.0%}  {etat}"
        )


@dataclass
class Corpus:
    """Les batailles enregistrees, et ce qu'elles valent."""

    battles: list[BattleHealth] = field(default_factory=list)

    #: Fichiers du repertoire qui ne sont pas des batailles reelles.
    ignored: int = 0

    @classmethod
    def load(cls, directory: Path) -> Corpus:
        """Lit les enregistrements de bataille **reelle** d'un repertoire.

        Le meme repertoire recoit les journaux du simulateur, au format tout
        different et bien plus nombreux — quatre cent quinze pour trois
        batailles reelles au moment ou ceci a ete ecrit. Seuls les fichiers qui
        se declarent comme tels sont retenus.
        """
        base = Path(directory)
        chemins = sorted(base.glob("*.jsonl")) if base.is_dir() else []
        batailles, ignores = [], 0
        for chemin in chemins:
            if _is_live_recording(chemin):
                batailles.append(inspect(chemin))
            else:
                ignores += 1
        return cls(battles=batailles, ignored=ignores)

    @property
    def usable(self) -> list[BattleHealth]:
        return [battle for battle in self.battles if battle.usable]

    @property
    def rejected(self) -> list[BattleHealth]:
        return [battle for battle in self.battles if not battle.usable]

    def render(self) -> str:
        """Tableau lisible dans un terminal."""
        if not self.battles:
            suffixe = (
                f"\n  ({self.ignored} journaux de simulation ignores : ce ne sont pas"
                " des batailles reelles.)"
                if self.ignored
                else ""
            )
            return (
                "Aucune bataille reelle enregistree.\n"
                "  `totalwar-ai probe --observe 2400` pendant une escarmouche en cree une."
                + suffixe
            )
        lignes = [
            f"{len(self.battles)} bataille(s), dont {len(self.usable)} exploitable(s)",
            "",
            f"{'bataille':10}{'etats':>7}{'decisions':>11}{'duree':>8}"
            f"{'unites':>8}{'note':>7}  etat",
        ]
        # Un corpus de trente batailles se lit ; trois cents ne se lisent plus.
        montrees = self.battles[:_MAX_LISTED]
        lignes += [f"  {battle.explain()}" for battle in montrees]
        if len(self.battles) > len(montrees):
            lignes.append(f"  ... et {len(self.battles) - len(montrees)} autre(s)")
        if self.rejected:
            lignes += [
                "",
                f"{len(self.rejected)} bataille(s) ecartee(s) par l'apprentissage.",
                "  Une bataille menee a son terme, unites enregistrees et flux continu,",
                "  vaut trois batailles interrompues.",
            ]
        return "\n".join(lignes)


def _is_live_recording(path: Path) -> bool:
    """Le fichier se declare-t-il comme une bataille reelle ?"""
    from totalwar_ai.bridge.recording import RECORDING_FORMAT

    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            premiere = handle.readline()
    except OSError:
        return False
    try:
        entete = json.loads(premiere)
    except json.JSONDecodeError:
        return False
    return isinstance(entete, dict) and entete.get("format") == RECORDING_FORMAT


def inspect(path: Path) -> BattleHealth:
    """Fiche de sante d'un enregistrement, sans jamais lever."""
    battle_id = path.stem
    try:
        lignes = list(_read(path))
    except OSError as error:
        return BattleHealth(path=path, battle_id=battle_id, error=str(error))

    # Un tour porte `turn` ; l'en-tete de format et les inventaires, non.
    tours = [ligne for ligne in lignes if "turn" in ligne and "roster" not in ligne]
    if not tours:
        return BattleHealth(path=path, battle_id=battle_id, error="aucun tour enregistre")

    sequences = [
        int(ligne["sequence"]) for ligne in tours if isinstance(ligne.get("sequence"), int)
    ]
    # Un saut de sequence signale des etats publies par la sonde et perdus en
    # route. Sans la sequence enregistree, ce trou serait invisible.
    trous = 0
    if len(sequences) >= 2:
        trous = max(0, (max(sequences) - min(sequences) + 1) - len(sequences))

    temps = [int(ligne.get("game_time_ms", 0)) for ligne in tours]
    inventaire: set[str] = set()
    for ligne in lignes:
        inventaire.update((ligne.get("roster") or {}).keys())

    return BattleHealth(
        path=path,
        battle_id=battle_id,
        observations=len(tours),
        decisions=sum(1 for ligne in tours if ligne.get("decision")),
        duration=(max(temps) - min(temps)) / 1000.0 if temps else 0.0,
        units_seen=len(inventaire),
        gaps=trous,
        has_units=any("units" in ligne for ligne in tours),
        has_shadow=any("shadow" in ligne for ligne in tours),
        finished=any(ligne.get("phase") == "Complete" for ligne in tours),
    )


def _read(path: Path) -> Iterator[dict[str, Any]]:
    """Lignes JSON d'un enregistrement, les illisibles etant sautees.

    Une ligne tronquee — plantage du jeu en pleine ecriture — ne doit pas
    condamner tout le reste de la bataille.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
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

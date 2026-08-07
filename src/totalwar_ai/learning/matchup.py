"""A qui l'on avait affaire, et en quel nombre.

**Pourquoi ceci existe.** Huit batailles reelles, huit defaites, l'adversaire
finissant a 92 ou 100 % de ses unites debout. Lu comme un verdict sur le
pilotage, cela dirait que l'IA du jeu — puis notre supervision — jouent tres
mal. Il manque pourtant a ce raisonnement la seule chose qui permette de le
tenir : **savoir si la bataille etait gagnable**.

Les comptes etaient sous nos yeux sans etre lus. Douze unites alliees contre
treize, quatorze, quinze, parfois vingt ; et le nombre adverse **augmente** en
cours de bataille — l'ennemi recoit des renforts. Aucune tactique ne rattrape un
tel ecart, et aucune mesure de supervision ne veut rien dire dans une bataille
perdue d'avance.

Ce module publie le rapport de forces : effectifs de depart, renforts arrives de
chaque cote, composition par role. Il ne juge rien — il donne ce qu'il faut pour
juger.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import Side, UnitRole

#: Ecart d'effectif au-dela duquel la bataille n'est plus un test de tactique.
#:
#: Un tiers d'unites en plus est un avantage qu'aucune manoeuvre ne compense a
#: qualite egale. En deca, le pilotage peut encore faire la difference.
LOPSIDED_RATIO = 1.33


@dataclass
class Matchup:
    """Le rapport de forces d'une bataille."""

    ally_start: int = 0
    enemy_start: int = 0
    ally_total: int = 0
    enemy_total: int = 0
    ally_roles: dict[UnitRole, int] = field(default_factory=dict)
    enemy_roles: dict[UnitRole, int] = field(default_factory=dict)

    @property
    def ally_reinforcements(self) -> int:
        return max(0, self.ally_total - self.ally_start)

    @property
    def enemy_reinforcements(self) -> int:
        return max(0, self.enemy_total - self.enemy_start)

    @property
    def ratio(self) -> float:
        """Unites adverses par unite alliee, renforts compris."""
        return self.enemy_total / self.ally_total if self.ally_total else 0.0

    @property
    def lopsided(self) -> bool:
        """L'ecart suffit-il a decider la bataille avant qu'elle ne commence ?"""
        return self.ratio >= LOPSIDED_RATIO

    def render(self) -> str:
        if not self.ally_total and not self.enemy_total:
            return "Aucune unite dans cet enregistrement."
        lignes = [
            f"  nous  {self.ally_total:2d} unite(s)  "
            f"({self.ally_start} au depart, {self.ally_reinforcements} en renfort)",
            f"  eux   {self.enemy_total:2d} unite(s)  "
            f"({self.enemy_start} au depart, {self.enemy_reinforcements} en renfort)",
            "",
            f"  rapport de forces : {self.ratio:.2f} unite adverse par unite a nous",
            "",
            "  role                     nous   eux",
        ]
        for role in sorted(set(self.ally_roles) | set(self.enemy_roles)):
            lignes.append(
                f"  {role.value:<22} {self.ally_roles.get(role, 0):4d}  "
                f"{self.enemy_roles.get(role, 0):4d}"
            )
        if self.lopsided:
            lignes += [
                "",
                "**Cette bataille n'est pas un test de tactique.** L'ecart d'effectif",
                "  decide seul de l'issue, et aucune mesure de supervision n'y veut rien",
                "  dire. Pour juger du pilotage, il faut un affrontement equilibre.",
            ]
        return "\n".join(lignes)


def summarise(states: Iterable[BattleState]) -> Matchup:
    """Le rapport de forces, renforts compris.

    Compte les unites **vues au moins une fois vivantes**, et non celles du
    premier etat : les renforts arrivent en cours de bataille, et les ignorer
    ferait passer une infériorite numerique croissante pour un combat egal.
    """
    vues: dict[Side, set[str]] = {Side.ALLY: set(), Side.ENEMY: set()}
    roles: dict[Side, dict[UnitRole, int]] = {Side.ALLY: {}, Side.ENEMY: {}}
    depart = {Side.ALLY: 0, Side.ENEMY: 0}
    premier = True

    for etat in states:
        for camp in (Side.ALLY, Side.ENEMY):
            presentes = [unit for unit in etat.side_units(camp) if unit.is_alive]
            if premier:
                depart[camp] = len(presentes)
            for unit in presentes:
                if unit.id not in vues[camp]:
                    vues[camp].add(unit.id)
                    roles[camp][unit.role] = roles[camp].get(unit.role, 0) + 1
        premier = False

    return Matchup(
        ally_start=depart[Side.ALLY],
        enemy_start=depart[Side.ENEMY],
        ally_total=len(vues[Side.ALLY]),
        enemy_total=len(vues[Side.ENEMY]),
        ally_roles=roles[Side.ALLY],
        enemy_roles=roles[Side.ENEMY],
    )

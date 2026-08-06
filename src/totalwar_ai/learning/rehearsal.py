"""Ce que nos regles auraient fait d'une bataille deja jouee.

**Pourquoi ceci existe.** Une regle de supervision se juge sur deux questions,
et le banc ne repond qu'a la seconde :

1. **se declenche-t-elle en vraie bataille**, et a quel moment ?
2. le resultat s'en trouve-t-il ameliore ?

L'essai n° 11 a montre le cout de l'ignorer : la supervision a tourne une
bataille entiere sans **aucune** intervention — l'armee etait en melee pure, ni
artillerie ni tireurs — et cette session ne disait rien du tout de la
supervision. Il faut savoir qu'une regle a matiere a agir avant de conclure quoi
que ce soit de son absence d'effet.

Ce module rejoue les regles sur un enregistrement, **sans rien changer**. Il
donne le nombre d'etats ou chacune aurait eu matiere a intervenir, les unites
concernees, et l'instant du premier declenchement — rapporte a la premiere
deroute alliee, qui est le moment ou une bataille de Total War se decide.

.. rubric:: Ce que le compte ne dit pas

Un etat concerne n'est pas une reprise. La supervision reelle tient un delai de
garde et ne reprend pas la meme unite a chaque tour : quatre cents etats
concernes peuvent donner une dizaine de reprises. Le compte mesure **la
frequence de la situation**, pas celle de l'action.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.bridge.supervision import DEFAULT_RULES, SupervisionRule, Supervisor
from totalwar_ai.domain.battle_state import BattleState


@dataclass(frozen=True, slots=True)
class RuleFirings:
    """Ce qu'une regle aurait trouve dans une bataille."""

    rule: str
    #: Etats ou la regle avait matiere a intervenir.
    states: int = 0
    #: Unites distinctes concernees.
    units: int = 0
    #: Temps de jeu du premier declenchement, en secondes.
    first: float | None = None
    #: Unite du premier declenchement.
    first_unit: str = ""

    def explain(self) -> str:
        if not self.states:
            return f"{self.rule:<22} jamais — aucune matiere a intervenir"
        return (
            f"{self.rule:<22} {self.states:5d} etats concernes, "
            f"{self.units} unite(s), premiere a t={self.first:.0f}s sur {self.first_unit}"
        )


@dataclass
class Rehearsal:
    """Le passage a blanc des regles sur une bataille enregistree."""

    firings: list[RuleFirings] = field(default_factory=list)
    #: Temps de jeu de la premiere deroute alliee, s'il y en a eu une.
    first_rout: float | None = None
    #: Unite qui a rompu la premiere, et sa sante a cet instant.
    first_rout_unit: str = ""
    first_rout_health: float = 0.0

    @property
    def triggered(self) -> list[RuleFirings]:
        return [item for item in self.firings if item.states]

    def render(self) -> str:
        if not self.firings:
            return "Aucune regle evaluee : l'enregistrement ne porte aucun etat."
        lignes = ["  " + item.explain() for item in self.firings]

        if self.first_rout is None:
            lignes += ["", "Aucune unite alliee n'a rompu : rien a prevenir dans cette bataille."]
            return "\n".join(lignes)

        lignes += [
            "",
            f"Premiere deroute alliee : t={self.first_rout:.0f}s "
            f"({self.first_rout_unit}, {self.first_rout_health:.0%} de sante)",
        ]
        # **Une regle qui se declenche apres la deroute arrive trop tard.** C'est
        # la seule chose que ce module tranche, et elle ne demande aucune theorie.
        for item in self.triggered:
            assert item.first is not None
            avance = self.first_rout - item.first
            if avance > 0:
                lignes.append(f"  {item.rule} se declenche {avance:.0f} s avant.")
            else:
                lignes.append(f"  {item.rule} arrive {-avance:.0f} s trop tard.")
        return "\n".join(lignes)


def rehearse(
    states: Iterable[BattleState],
    *,
    rules: tuple[SupervisionRule, ...] = DEFAULT_RULES,
) -> Rehearsal:
    """Rejoue les regles sur une bataille, sans rien changer a rien."""
    comptes: dict[str, int] = {}
    unites: dict[str, set[str]] = {}
    premieres: dict[str, tuple[float, str]] = {}
    premiere_deroute: tuple[float, str, float] | None = None

    superviseur = Supervisor(rules=rules)
    for etat in states:
        allies = etat.allies(available_only=False)
        if premiere_deroute is None:
            for unit in allies:
                if unit.is_routing and unit.is_alive:
                    premiere_deroute = (etat.game_time, unit.id, unit.health_ratio)
                    break

        perimetre = {unit.id for unit in allies}
        for item in superviseur.review(etat, perimetre):
            comptes[item.rule] = comptes.get(item.rule, 0) + 1
            unites.setdefault(item.rule, set()).add(item.unit_id)
            premieres.setdefault(item.rule, (etat.game_time, item.unit_id))
        # Superviseur d'ombre : il ne tient aucune unite, et le laisser
        # accumuler l'empecherait de se declencher a nouveau au tour suivant.
        superviseur.forget(list(perimetre))

    return Rehearsal(
        firings=[
            RuleFirings(
                rule=rule.name,
                states=comptes.get(rule.name, 0),
                units=len(unites.get(rule.name, ())),
                first=premieres.get(rule.name, (None, ""))[0],
                first_unit=premieres.get(rule.name, (None, ""))[1],
            )
            for rule in rules
        ],
        first_rout=premiere_deroute[0] if premiere_deroute else None,
        first_rout_unit=premiere_deroute[1] if premiere_deroute else "",
        first_rout_health=premiere_deroute[2] if premiere_deroute else 0.0,
    )


def rout_cascade(states: Iterable[BattleState]) -> list[tuple[float, str, float]]:
    """Chaque unite alliee qui rompt, dans l'ordre, avec sa sante a cet instant.

    **C'est la lecture qui a renverse celle d'une bataille entiere.** Comptee en
    unites debout, l'armee semblait fondre ; comptee en unites detruites, elle
    n'en avait perdu **qu'une seule**. Huit sur douze avaient rompu, et six
    d'entre elles au-dessus de 39 % de sante — ce n'est pas de l'usure, c'est une
    contagion partie de deux unites laissees combattre jusqu'a 17 %.
    """
    rompues: dict[str, tuple[float, str, float]] = {}
    sante: dict[str, float] = {}
    for etat in states:
        for unit in etat.allies(available_only=False):
            if unit.is_routing and unit.id not in rompues:
                # La sante retenue est celle de l'etat **precedent** : au moment
                # ou elle rompt, l'unite a deja commence a se faire tailler.
                rompues[unit.id] = (etat.game_time, unit.id, sante.get(unit.id, unit.health_ratio))
            if not unit.is_routing:
                sante[unit.id] = unit.health_ratio
    return sorted(rompues.values())


def render_cascade(cascade: list[tuple[float, str, float]]) -> str:
    if not cascade:
        return "Aucune unite alliee n'a rompu."
    lignes = [f"{len(cascade)} unite(s) alliee(s) ont rompu :", ""]
    lignes += [
        f"  t={temps:6.0f}s  {unit_id:>6}  {sante:4.0%} de sante"
        for temps, unit_id, sante in cascade
    ]
    hautes = [item for item in cascade if item[2] > 0.4]
    if hautes:
        lignes += [
            "",
            f"{len(hautes)} ont rompu au-dessus de 40 % de sante : "
            "ce n'est pas de l'usure, c'est une contagion.",
        ]
    return "\n".join(lignes)

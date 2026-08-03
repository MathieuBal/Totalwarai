"""Ce que chaque unite a fait de sa bataille.

**Pourquoi ceci existe.** L'operateur a vu son armee « pousser, mais pas avec
toutes les unites », et trois unites de tir rester en arriere. Deux explications
tiennent le meme fait, et elles appellent des corrections opposees :

* elles **n'ont jamais ete confiees** a l'IA du jeu, et sont restees plantees
  la ou elles etaient deployees — defaut de notre cote ;
* elles ont bien ete confiees, et l'IA du jeu les a **volontairement gardees en
  arriere pour tirer** — comportement normal, et notre lecture qui etait fausse.

Aucun compte global ne separe les deux. Le trajet, les munitions depensees et le
temps passe au contact, unite par unite, les separent en une ligne.

.. rubric:: Une unite inerte n'est pas une unite prudente

Une unite qui n'a ni bouge, ni tire, ni combattu de toute la bataille n'a pas
choisi la retenue : elle n'a recu aucun ordre. C'est le seul verdict que ce
module prononce, et il ne repose sur aucune interpretation tactique.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side, UnitRole

#: Deplacement en deca duquel un ecart entre deux etats est du tassement.
#:
#: A 2 Hz, une unite immobile bouge encore de quelques dizaines de centimetres :
#: les additionner sur huit cents etats donnerait des centaines de metres
#: parcourus par une unite qui n'a jamais quitte sa place.
STEP_NOISE = 1.0

#: Trajet en deca duquel une unite n'a pas manoeuvre, en metres.
IDLE_DISTANCE = 30.0

#: Part de munitions en deca de laquelle une unite de tir n'a pas tire.
IDLE_AMMO = 0.05


@dataclass(frozen=True, slots=True)
class UnitActivity:
    """Le compte rendu d'une unite, du deploiement a la fin."""

    unit_id: str
    role: UnitRole
    #: Chemin parcouru, en metres, bruit de formation deduit.
    travelled: float = 0.0
    #: Part de la dotation depensee, dans [0, 1]. Zero pour une unite de melee.
    ammo_spent: float = 0.0
    #: Part du temps passee au contact, dans [0, 1].
    melee_share: float = 0.0
    #: Etats ou l'unite etait vivante.
    seen: int = 0
    #: L'unite a-t-elle fini la bataille debout ?
    survived: bool = True

    @property
    def inert(self) -> bool:
        """N'a ni manoeuvre, ni tire, ni combattu.

        Ce n'est pas de la prudence : c'est une unite qui n'a recu aucun ordre.
        """
        return (
            self.travelled < IDLE_DISTANCE and self.ammo_spent < IDLE_AMMO and self.melee_share <= 0
        )

    def explain(self) -> str:
        munitions = f"{self.ammo_spent:5.0%}" if self.role in RANGED_ROLES else "    —"
        verdict = "  INERTE : aucun ordre recu" if self.inert else ""
        mort = "" if self.survived else "  (detruite)"
        return (
            f"{self.unit_id:>6}  {self.role.value:<16} "
            f"{self.travelled:6.0f} m  {munitions} tire  "
            f"{self.melee_share:5.0%} au contact{mort}{verdict}"
        )


@dataclass
class ActivityReport:
    """Toute l'armee, unite par unite."""

    units: list[UnitActivity] = field(default_factory=list)
    states: int = 0
    duration: float = 0.0

    @property
    def inert(self) -> list[UnitActivity]:
        return [unit for unit in self.units if unit.inert]

    def render(self) -> str:
        if not self.units:
            return "Aucune unite alliee dans cet enregistrement."
        lignes = [
            f"{len(self.units)} unite(s) alliee(s), {self.states} etats, {self.duration:.0f} s",
            "",
        ]
        # Les plus immobiles en tete : c'est ce qu'on vient chercher.
        lignes += [
            f"  {unit.explain()}" for unit in sorted(self.units, key=lambda item: item.travelled)
        ]
        if self.inert:
            lignes += [
                "",
                f"{len(self.inert)} unite(s) n'ont ni manoeuvre, ni tire, ni combattu.",
                "  Une unite inerte n'a pas choisi la retenue : elle n'a recu aucun ordre.",
                "  Verifier qu'elle figurait bien parmi les unites confiees au demarrage.",
            ]
        else:
            lignes += ["", "Toutes les unites ont agi : aucune n'est restee sans ordre."]
        return "\n".join(lignes)


def summarise(states: Iterable[BattleState], *, side: Side = Side.ALLY) -> ActivityReport:
    """Ce que chaque unite d'un camp a fait, etat apres etat."""
    trajets: dict[str, float] = {}
    roles: dict[str, UnitRole] = {}
    vus: dict[str, int] = {}
    contacts: dict[str, int] = {}
    munitions_hautes: dict[str, float] = {}
    munitions_basses: dict[str, float] = {}
    vivantes: dict[str, bool] = {}

    compte = 0
    debut = fin = 0.0
    dernier: BattleState | None = None
    for etat in states:
        compte += 1
        if dernier is None:
            debut = etat.game_time
        fin = etat.game_time
        for unit in etat.side_units(side):
            roles[unit.id] = unit.role
            vivantes[unit.id] = unit.is_alive
            if not unit.is_alive:
                continue
            vus[unit.id] = vus.get(unit.id, 0) + 1
            if unit.is_engaged:
                contacts[unit.id] = contacts.get(unit.id, 0) + 1
            # Le maximum vu est la dotation ; le minimum, ce qu'il en reste.
            haut = munitions_hautes.get(unit.id)
            munitions_hautes[unit.id] = (
                unit.ammo_ratio if haut is None else max(haut, unit.ammo_ratio)
            )
            bas = munitions_basses.get(unit.id)
            munitions_basses[unit.id] = (
                unit.ammo_ratio if bas is None else min(bas, unit.ammo_ratio)
            )
            if dernier is not None:
                avant = dernier.unit(unit.id)
                if avant is not None:
                    pas = avant.position.distance_2d(unit.position)
                    # Sous le seuil, c'est du tassement de formation : l'additionner
                    # sur huit cents etats ferait parcourir des centaines de metres
                    # a une unite qui n'a jamais quitte sa place.
                    if pas >= STEP_NOISE:
                        trajets[unit.id] = trajets.get(unit.id, 0.0) + pas
        dernier = etat

    return ActivityReport(
        units=[
            UnitActivity(
                unit_id=unit_id,
                role=role,
                travelled=trajets.get(unit_id, 0.0),
                ammo_spent=max(
                    0.0, munitions_hautes.get(unit_id, 0.0) - munitions_basses.get(unit_id, 0.0)
                ),
                melee_share=contacts.get(unit_id, 0) / vus[unit_id] if vus.get(unit_id) else 0.0,
                seen=vus.get(unit_id, 0),
                survived=vivantes.get(unit_id, False),
            )
            for unit_id, role in sorted(roles.items())
        ],
        states=compte,
        duration=max(0.0, fin - debut),
    )

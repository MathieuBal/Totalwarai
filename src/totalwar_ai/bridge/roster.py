"""Effectifs initiaux des unites, deduits de l'observation.

Le jeu n'expose pas `number_of_men` — l'effectif nominal d'une unite. Il ne
donne que `number_of_men_alive`, le nombre de survivants. Sans denominateur,
impossible de dire si quarante hommes debout sont une unite intacte ou une unite
a moitie detruite.

La solution ne demande rien au jeu : au deploiement, une unite est au complet.
Le **maximum observe** depuis le debut de la bataille est donc son effectif
initial. Une unite ne regagne pas d'hommes, ce maximum ne peut que rester juste.

Pourquoi ne pas se contenter de `unary_hitpoints` ? Parce que sa signification
sur une unite de plusieurs dizaines d'hommes n'est pas etablie : il peut valoir
la fraction d'unite restante, ou la sante moyenne des survivants. Les deux
divergent completement — une unite reduite a dix hommes en pleine forme vaut
0,12 dans un cas et 1,0 dans l'autre. Compter les hommes ne souffre pas de cette
ambiguite.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation


@dataclass
class RosterMemory:
    """Retient l'effectif le plus eleve vu pour chaque unite."""

    #: identifiant d'unite -> plus grand `men_alive` observe.
    initial: dict[str, int] = field(default_factory=dict)
    #: identifiant d'unite -> plus grand `ammo_left` observe.
    initial_ammo: dict[str, int] = field(default_factory=dict)

    def observe(self, state: ProbeBattleState) -> None:
        """Met a jour la memoire avec un nouvel etat de bataille."""
        for observation in (*state.allies, *state.enemies):
            self._observe_unit(observation)

    def _observe_unit(self, observation: ProbeUnitObservation) -> None:
        if observation.men_alive is not None:
            known = self.initial.get(observation.unit_id)
            if known is None or observation.men_alive > known:
                self.initial[observation.unit_id] = observation.men_alive
        if observation.ammo is not None:
            known_ammo = self.initial_ammo.get(observation.unit_id)
            if known_ammo is None or observation.ammo > known_ammo:
                self.initial_ammo[observation.unit_id] = observation.ammo

    def initial_men(self, unit_id: str) -> int | None:
        """Effectif initial connu, ou `None` si l'unite n'a jamais ete vue."""
        return self.initial.get(unit_id)

    def entity_ratio(self, observation: ProbeUnitObservation) -> float | None:
        """Fraction d'unite encore debout, dans [0, 1].

        Renvoie `None` quand le compte d'hommes n'est pas disponible : l'appelant
        doit alors se rabattre sur autre chose plutot que sur un chiffre invente.
        """
        if observation.men_alive is None:
            return None
        initial = self.initial.get(observation.unit_id)
        if initial is None or initial <= 0:
            return None
        return min(1.0, observation.men_alive / initial)

    def ammo_ratio(self, observation: ProbeUnitObservation) -> float | None:
        """Fraction de munitions restantes, dans [0, 1].

        Le jeu donne un total — `ammo_left` valait 480 pour une unite de 120
        tireurs — la ou le domaine attend un rapport. Meme raisonnement que pour
        les effectifs : le maximum observe est la dotation de depart, une unite
        ne se reapprovisionnant pas en bataille.

        Renvoie `None` quand le jeu ne donne pas la munition, pour que
        l'appelant distingue « a court » de « inconnu » : les deux menent a des
        decisions opposees.
        """
        if observation.ammo is None:
            return None
        initial = self.initial_ammo.get(observation.unit_id)
        if initial is None or initial <= 0:
            return None
        return min(1.0, observation.ammo / initial)

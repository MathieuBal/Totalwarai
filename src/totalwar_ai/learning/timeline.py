"""Ou une bataille a bascule, et ce qui s'y passait.

**Pourquoi ceci existe.** Un compte de fin de bataille — « 58 % de nos unites
debout, 100 % des leurs » — dit qu'on a perdu, jamais pourquoi. Trois defaites
identiques au tableau peuvent avoir trois causes differentes : une ligne qui
cede, des tireurs pris de flanc, un seigneur mort trop tot.

Ce module decoupe la bataille en tranches et publie, pour chacune, ce qui se
voit : force restante des deux cotes, unites au contact, tireurs a couvert ou
non, distance des deux armees. La bascule est la tranche ou l'ecart de force se
creuse le plus vite.

.. rubric:: Ce qu'il ne fait pas

Il ne dit pas la cause. Une force qui s'effondre pendant que les tireurs sont au
contact **suggere** que la ligne a cede ; elle ne le prouve pas. Le module
publie ce qui coincide, et laisse conclure — c'est la meme regle que partout
ailleurs ici : une correlation nommee vaut mieux qu'une cause inventee.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.unit_state import RANGED_ROLES, Side, UnitRole

#: Tranches en lesquelles la bataille est decoupee.
#:
#: Assez pour voir une bascule, assez peu pour tenir dans un terminal.
SLICES = 12

#: Part de la pire chute a partir de laquelle une tranche marque le debut.
#:
#: Un quart suffit a distinguer le choc de la deroute qui suit : la bataille
#: reelle analysee perdait 14 points au contact, puis 16 en pleine fuite.
ONSET_SHARE = 0.25


@dataclass(frozen=True, slots=True)
class Slice:
    """Une tranche de bataille, et ce qui s'y voyait."""

    start: float
    end: float
    #: Unites alliees **encore au combat**, en part de l'effectif de depart.
    #:
    #: Ni detruites, ni en deroute : une unite qui fuit ne pese plus rien dans
    #: la bataille, et la compter ferait passer un effondrement pour une usure.
    ally_strength: float
    enemy_strength: float
    #: Unites alliees au contact, en part des vivantes.
    engaged_share: float
    #: Tireurs allies au contact — ils y perdent leur raison d'etre.
    ranged_in_melee: int
    #: Distance moyenne entre les deux armees, en metres.
    separation: float
    #: Le seigneur est-il encore debout ?
    lord_alive: bool = True

    @property
    def edge(self) -> float:
        """Avantage allie : positif si l'on tient mieux qu'eux."""
        return self.ally_strength - self.enemy_strength

    def explain(self) -> str:
        tireurs = f" {self.ranged_in_melee} tireur(s) au contact" if self.ranged_in_melee else ""
        seigneur = "" if self.lord_alive else "  SEIGNEUR TOMBE"
        return (
            f"{self.start:5.0f}-{self.end:3.0f}s  "
            f"nous {self.ally_strength:4.0%}  eux {self.enemy_strength:4.0%}  "
            f"ecart {self.edge:+5.0%}  "
            f"{self.engaged_share:4.0%} au contact  "
            f"{self.separation:4.0f} m{tireurs}{seigneur}"
        )


@dataclass
class Timeline:
    """Le deroule d'une bataille, tranche par tranche."""

    slices: list[Slice] = field(default_factory=list)

    @property
    def turning_point(self) -> Slice | None:
        """La tranche ou la bataille a commence a basculer.

        **Le pire moment n'est pas le moment decisif.** Sur la premiere bataille
        reelle analysee, la plus forte chute tombait a 490-524 s — l'armee etait
        alors deja en pleine deroute, sans une seule unite au contact, et rien
        d'interessant ne s'y passait. La bataille s'etait perdue cent secondes
        plus tot, au choc.

        On cherche donc le **debut** de la degradation : la premiere tranche
        dont la chute atteint une part notable de la pire.
        """
        chutes = self._drops()
        if not chutes:
            return None
        pire = max(chute for chute, _ in chutes)
        if pire <= 0:
            return None
        for chute, tranche in chutes:
            if chute >= pire * ONSET_SHARE:
                return tranche
        return None

    @property
    def collapse(self) -> Slice | None:
        """La tranche ou l'ecart s'est creuse le plus vite."""
        chutes = self._drops()
        if not chutes:
            return None
        chute, tranche = max(chutes, key=lambda item: item[0])
        return tranche if chute > 0 else None

    def _drops(self) -> list[tuple[float, Slice]]:
        if len(self.slices) < 2:
            return []
        return [
            (self.slices[index - 1].edge - self.slices[index].edge, self.slices[index])
            for index in range(1, len(self.slices))
        ]

    def render(self) -> str:
        if not self.slices:
            return "Bataille trop courte pour un deroule."
        bascule = self.turning_point
        lignes = [
            "  (« encore au combat » : ni detruites, ni en deroute)",
            "",
        ]
        lignes += ["  " + item.explain() for item in self.slices]
        if bascule is None:
            lignes += ["", "Aucune bascule : l'ecart n'a jamais recule d'une tranche a l'autre."]
            return "\n".join(lignes)

        lignes += [
            "",
            f"Bascule vers {bascule.start:.0f}-{bascule.end:.0f} s : "
            "c'est la que la degradation commence.",
        ]
        lignes += [f"  {ligne}" for ligne in _read_slice(bascule)]
        effondrement = self.collapse
        if effondrement is not None and effondrement is not bascule:
            lignes.append(
                f"  Le pire passe a {effondrement.start:.0f}-{effondrement.end:.0f} s, "
                "mais tout y etait deja joue."
            )
        return "\n".join(lignes)


def _read_slice(tranche: Slice) -> list[str]:
    """Ce qui coincide avec la bascule. **Ce ne sont pas des causes.**"""
    notes: list[str] = []
    if tranche.ranged_in_melee:
        notes.append(
            f"{tranche.ranged_in_melee} tireur(s) etaient au contact : "
            "la ou ils ne servent plus a rien et meurent vite."
        )
    if not tranche.lord_alive:
        notes.append("le seigneur etait deja tombe.")
    if tranche.engaged_share > 0.7:
        notes.append(
            f"{tranche.engaged_share:.0%} de l'armee etait engagee : "
            "plus aucune reserve pour repondre."
        )
    elif tranche.engaged_share < 0.3 and tranche.separation < 80.0:
        notes.append(
            f"seulement {tranche.engaged_share:.0%} de l'armee etait engagee a "
            f"{tranche.separation:.0f} m : l'attaque n'etait pas menee en entier."
        )
    if not notes:
        notes.append("rien de remarquable ne coincide : la ligne a simplement cede.")
    return ["Ce qui coincide (et non ce qui cause) :", *[f"  - {note}" for note in notes]]


def summarise(states: Iterable[BattleState], *, slices: int = SLICES) -> Timeline:
    """Decoupe une bataille et publie ce qui se voit dans chaque tranche."""
    retenus = [etat for etat in states if etat.units]
    if len(retenus) < slices:
        return Timeline()

    debut, fin = retenus[0].game_time, retenus[-1].game_time
    if fin <= debut:
        return Timeline()
    largeur = (fin - debut) / slices

    # Force initiale : le nombre d'unites vivantes au premier etat. Les points
    # de vie ne sont pas comparables entre un seigneur et une pietaille ; le
    # compte d'unites l'est, et c'est ce que l'operateur lit a l'ecran.
    depart_allie = len(retenus[0].side_units(Side.ALLY, available_only=True)) or 1
    depart_ennemi = len(retenus[0].side_units(Side.ENEMY, available_only=True)) or 1

    tranches: list[Slice] = []
    for index in range(slices):
        borne_basse = debut + index * largeur
        borne_haute = borne_basse + largeur
        dedans = [etat for etat in retenus if borne_basse <= etat.game_time < borne_haute]
        if not dedans:
            continue
        tranches.append(_slice(dedans, borne_basse, borne_haute, depart_allie, depart_ennemi))
    return Timeline(slices=tranches)


def _slice(
    etats: list[BattleState],
    debut: float,
    fin: float,
    depart_allie: int,
    depart_ennemi: int,
) -> Slice:
    allies = engages = tireurs_au_contact = 0
    ennemis = 0
    separation = 0.0
    seigneur_debout = False
    for etat in etats:
        vivants = etat.side_units(Side.ALLY, available_only=True)
        adverses = etat.side_units(Side.ENEMY, available_only=True)
        allies += len(vivants)
        ennemis += len(adverses)
        engages += sum(1 for unit in vivants if unit.is_engaged)
        tireurs_au_contact = max(
            tireurs_au_contact,
            sum(1 for unit in vivants if unit.role in RANGED_ROLES and unit.is_engaged),
        )
        seigneur_debout = seigneur_debout or any(unit.role is UnitRole.LORD for unit in vivants)
        if vivants and adverses:
            separation += etat.centroid(Side.ALLY).distance_2d(etat.centroid(Side.ENEMY))

    compte = len(etats)
    return Slice(
        start=debut,
        end=fin,
        ally_strength=allies / compte / depart_allie,
        enemy_strength=ennemis / compte / depart_ennemi,
        engaged_share=engages / allies if allies else 0.0,
        ranged_in_melee=tireurs_au_contact,
        separation=separation / compte,
        lord_alive=seigneur_debout,
    )

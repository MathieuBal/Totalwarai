"""Apprendre ou l'IA du moteur place ses unites, role par role.

**Ce que ce module cherche.** Le ciblage dit *qui* attaquer ; il ne dit pas ou
se tenir. Or c'est la que notre agent est le plus faible : il ne voit ni le
terrain ni les formations, et place ses unites d'apres des constantes ecrites a
la main. L'IA du moteur, elle, place les siennes en connaissance de cause — et
cela s'observe.

.. rubric:: Tout est relatif, sinon rien ne transfere

Une position absolue ne veut rien dire d'une carte a l'autre. Ce qui se
transporte, c'est la place d'un role **dans sa propre armee** :

* la **profondeur** — distance le long de l'axe qui va du centre de notre armee
  a celui de l'adversaire. Positive vers l'ennemi. C'est elle qui dit si les
  tireurs se tiennent derriere la ligne, et de combien ;
* l'**ecart au centre** — distance laterale a ce meme axe. C'est elle qui dit si
  la cavalerie se tient aux ailes ;
* l'**espacement** — distance a l'allie le plus proche, qui dit si la ligne est
  serree ou etalee.

.. rubric:: Une formation ne se mesure qu'avant le contact

Des que la melee commence, les unites s'interpenetrent et la formation cesse
d'exister : mesurer la place des tireurs pendant une melee generale reviendrait
a mesurer du desordre. Seuls les etats **d'approche** sont retenus — les deux
armees separees, aucun contact engage.

C'est le meme constat que pour le ciblage, ou la melee n'est pas un choix
(ADR 0008). Une bataille se lit dans les instants qui precedent le choc.

.. rubric:: Ce que cela ne donne pas

Le relief. Une unite peut se tenir en retrait parce que c'est tactiquement bon,
ou parce qu'une colline l'y oblige. Nos donnees ne permettent pas de trancher —
tout au plus l'altitude enregistree dira-t-elle un jour si les tireurs cherchent
la hauteur. La moyenne d'un module n'est pas une explication.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3, centroid
from totalwar_ai.domain.unit_state import Side, UnitRole, UnitState

#: Distance en deca de laquelle deux armees sont considerees au contact.
#:
#: Sous ce seuil, la formation ne veut plus rien dire : les lignes se sont
#: melangees et l'on mesurerait du desordre.
CONTACT_DISTANCE = 60.0

#: Relevés en deca desquels une moyenne ne se lit pas.
#:
#: Une unite vue trois fois donne une moyenne qui ne dit que le bruit du corpus.
MINIMUM_SAMPLES = 20


@dataclass(frozen=True, slots=True)
class RolePlacement:
    """Ou un role se tient, dans son armee, avant le contact."""

    role: UnitRole
    samples: int
    #: Le long de l'axe vers l'ennemi, en metres. Positif = devant le centre.
    depth: float
    depth_spread: float
    #: Distance laterale a cet axe, en metres. Toujours positive.
    flank: float
    #: Distance a l'allie le plus proche, en metres.
    spacing: float

    @property
    def solid(self) -> bool:
        return self.samples >= MINIMUM_SAMPLES

    def explain(self) -> str:
        place = "devant" if self.depth > 0 else "en retrait"
        doute = "" if self.solid else "  (trop peu de releves)"
        return (
            f"{self.role.value:<16} {abs(self.depth):5.1f} m {place:<11} "
            f"+/- {self.depth_spread:4.1f}   "
            f"{self.flank:5.1f} m du centre   "
            f"{self.spacing:5.1f} m de l'allie le plus proche"
            f"   ({self.samples} releves){doute}"
        )


@dataclass
class GeometryModel:
    """La forme que l'IA observee donne a son armee."""

    placements: dict[UnitRole, RolePlacement] = field(default_factory=dict)

    #: Etats d'approche retenus.
    samples: int = 0
    #: Etats ecartes : contact engage, ou un camp absent.
    skipped: int = 0

    #: Distance moyenne entre les deux armees sur les etats retenus.
    separation: float = 0.0

    def placement(self, role: UnitRole) -> RolePlacement | None:
        return self.placements.get(role)

    def ordered(self) -> list[RolePlacement]:
        """Du plus avance au plus en retrait — l'ordre de bataille observe."""
        return sorted(self.placements.values(), key=lambda item: item.depth, reverse=True)

    def render(self) -> str:
        if not self.placements:
            return (
                "Aucune formation mesurable : le corpus ne contient aucun etat "
                "d'approche (armees separees, hors contact)."
            )
        lignes = [
            f"{self.samples} etat(s) d'approche retenu(s), {self.skipped} ecarte(s) "
            f"— armees a {self.separation:.0f} m l'une de l'autre en moyenne",
            "",
            "  ordre de bataille observe, du plus avance au plus en retrait :",
        ]
        lignes += [f"    {item.explain()}" for item in self.ordered()]
        return "\n".join(lignes)


def learn_formation(
    states: Iterable[BattleState],
    *,
    side: Side = Side.ALLY,
) -> GeometryModel:
    """Mesure la place de chaque role, etat d'approche apres etat d'approche.

    **Consomme les etats a la volee, sans jamais les garder.** Trente batailles
    a deux hertz font plusieurs centaines de milliers d'etats d'unite ; les
    empiler pour en prendre la moyenne a la fin couterait des centaines de
    mega-octets pour quatre nombres par role. On accumule des sommes.
    """
    tallies: dict[UnitRole, _Tally] = {}
    retenus = ecartes = 0
    separation = 0.0

    for state in states:
        mesure = _measure(state, side)
        if mesure is None:
            ecartes += 1
            continue
        retenus += 1
        separation += mesure.separation
        for role, valeurs in mesure.per_role:
            tallies.setdefault(role, _Tally()).add(*valeurs)

    return GeometryModel(
        placements={role: tally.placement(role) for role, tally in tallies.items() if tally.count},
        samples=retenus,
        skipped=ecartes,
        separation=separation / retenus if retenus else 0.0,
    )


@dataclass
class _Tally:
    """Sommes courantes pour un role. Memoire constante, quel que soit le corpus."""

    count: int = 0
    depth: float = 0.0
    depth_squared: float = 0.0
    flank: float = 0.0
    spacing: float = 0.0

    def add(self, depth: float, flank: float, spacing: float) -> None:
        self.count += 1
        self.depth += depth
        self.depth_squared += depth * depth
        self.flank += flank
        self.spacing += spacing

    def placement(self, role: UnitRole) -> RolePlacement:
        moyenne = self.depth / self.count
        return RolePlacement(
            role=role,
            samples=self.count,
            depth=moyenne,
            depth_spread=self._spread(moyenne),
            flank=self.flank / self.count,
            spacing=self.spacing / self.count,
        )

    def _spread(self, moyenne: float) -> float:
        """Ecart-type d'echantillon, ou zero quand il n'y a qu'un releve.

        Une dispersion large dit qu'un role n'a **pas** de place fixe. C'est une
        information a part entiere : une moyenne publiee sans elle ferait passer
        une unite qui va partout pour une unite qui se tient quelque part.
        """
        if self.count < 2:
            return 0.0
        # `max(0, ...)` : la somme des carres moins le carre de la somme peut
        # devenir infiniment negative en virgule flottante quand la dispersion
        # est nulle. Une variance negative ferait lever la racine.
        variance = max(0.0, self.depth_squared - self.count * moyenne * moyenne) / (self.count - 1)
        return float(variance**0.5)


@dataclass(frozen=True, slots=True)
class _Measure:
    separation: float
    #: (role, (profondeur, ecart au centre, espacement)) pour chaque unite.
    per_role: list[tuple[UnitRole, tuple[float, float, float]]]


def _measure(state: BattleState, side: Side) -> _Measure | None:
    """Un etat d'approche, ou `None` si la formation n'y veut rien dire."""
    amis = [unit for unit in state.side_units(side, available_only=True)]
    adverse = Side.ENEMY if side is Side.ALLY else Side.ALLY
    ennemis = [unit for unit in state.side_units(adverse, available_only=True)]
    if len(amis) < 2 or not ennemis:
        return None
    # Une seule unite au contact suffit a defaire la formation autour d'elle.
    if any(unit.is_engaged for unit in amis):
        return None

    notre_centre = centroid(unit.position for unit in amis)
    leur_centre = centroid(unit.position for unit in ennemis)
    separation = notre_centre.distance_2d(leur_centre)
    if separation < CONTACT_DISTANCE:
        return None

    axe = notre_centre.direction_to(leur_centre)
    par_role: list[tuple[UnitRole, tuple[float, float, float]]] = []
    for unit in amis:
        profondeur, ecart = _project(unit.position - notre_centre, axe)
        espacement = _nearest_ally(unit, amis)
        par_role.append((unit.role, (profondeur, ecart, espacement)))
    return _Measure(separation=separation, per_role=par_role)


def _project(offset: Vector3, axis: Vector3) -> tuple[float, float]:
    """Decompose un ecart en (le long de l'axe, perpendiculaire a l'axe).

    Travail a plat : l'altitude renseigne le relief, pas la formation, et la
    faire entrer ici melangerait deux mesures qui ne disent pas la meme chose.
    """
    le_long = offset.x * axis.x + offset.z * axis.z
    # Norme du reste, par Pythagore : plus court et plus sur qu'un produit
    # vectoriel a ecrire a la main.
    plat = (offset.x * offset.x + offset.z * offset.z) ** 0.5
    lateral = max(0.0, plat * plat - le_long * le_long) ** 0.5
    return le_long, lateral


def _nearest_ally(unit: UnitState, amis: Sequence[UnitState]) -> float:
    distances = [unit.position.distance_2d(autre.position) for autre in amis if autre.id != unit.id]
    return min(distances) if distances else 0.0

"""Ce qu'un secteur vaut vraiment, mesure et non decide.

**Pourquoi ceci existe.** Une fois les instruments repares (ADR 0019), le seul
secteur de `skirmish_standoff` a atteindre le rapport requis est un regiment
**isole**, a 4,92 contre 1,17 et 1,23 ailleurs. L'enfoncer transforme le nul en
defaite : 74 % de nos forces restantes contre 98 % des leurs.

L'agent sait repondre a « ou suis-je le plus fort ? ». Il ne sait pas encore
demander « et ensuite ? ». Mais avant d'ecrire un score, il faut savoir **quel
attribut** aurait ecarte ce secteur-la sans ecarter les assauts que l'agent gagne
deja — et cela se mesure.

.. rubric:: Ce module mesure, il ne decide pas

Il porte volontairement un autre nom que :mod:`totalwar_ai.agent.sectors`, qui
decide. Le depot separe deja `learning.concentration` de `agent.sectors` pour
cette raison, et trois fois cette session c'est la mesure independante qui a
contredit la doctrine : elle ne le peut que si les deux ne partagent pas leur
code.

.. rubric:: Le probleme de donnees, et le plan d'experience

`best()` ne choisit qu'un secteur par etat, si bien qu'aucune donnee n'existe sur
ce que les autres auraient donne. La sonde rejoue donc la meme bataille en
**imposant** chaque secteur tour a tour (`Planner.forced_sector`), fenetre
d'arrivee glissante activee — sans quoi `commit()` refuse tout et il n'y a rien a
observer.

Deux honnetetes, a publier avec les chiffres :

* la mesure repond a « ce que ce secteur donnerait **si l'on pouvait y composer
  un assaut** », ce qui est la question posee, et non a « ce que l'agent ferait » ;
* forcer un secteur n'est pas jouer : ces batailles ne comptent dans aucun taux
  de victoire, et le banc ne bouge pas.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from totalwar_ai.agent.sectors import BREAK_SHARE, SUPPORT_RADIUS
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import centroid
from totalwar_ai.domain.unit_state import RANGED_ROLES, UnitRole, UnitState
from totalwar_ai.telemetry.events import Event, EventType

if TYPE_CHECKING:  # pragma: no cover - uniquement pour le typage
    from totalwar_ai.config import AppConfig
    from totalwar_ai.simulation.scenarios import Scenario

#: Nombre de **configurations distinctes** sous lequel on ne conclut rien.
#:
#: **Ce seuil portait d'abord sur le nombre de releves, et c'etait faux.** Le
#: premier passage en a produit 96, ce qui paraissait confortable ; ils ne
#: portaient que quatre formes de secteur — une par scenario — repetees sur douze
#: graines et trois indices. Un attribut pouvait alors separer parfaitement les
#: bons des mauvais echanges en ne designant rien d'autre que le scenario.
#:
#: Huit configurations ne sont pas un grand nombre. C'est deliberement bas :
#: l'enjeu n'est pas la puissance statistique, c'est d'empecher une lecture de
#: conclure sur quatre points.
MINIMUM_CONFIGURATIONS = 8

#: Roles dont la presence dans un secteur en fait une cible de valeur.
HIGH_VALUE_ROLES = frozenset(
    {UnitRole.ARTILLERY, UnitRole.LORD, UnitRole.HERO_CASTER, UnitRole.HERO_MELEE}
)


@dataclass(frozen=True, slots=True)
class SectorRecord:
    """Un assaut observe : ce que le secteur promettait, et ce qu'il a rendu."""

    scenario: str
    seed: int
    sector: int
    started_at: float

    # --- ce que le secteur promettait, au moment du choix -------------------
    #: Rapport local annonce par la primitive.
    ratio: float
    #: Part de la force adverse totale que ce secteur represente.
    enemy_share: float
    #: Ennemis du secteur sans aucun voisin adverse a portee de soutien.
    isolated: int
    #: Nombre d'ennemis dans le secteur.
    enemies: int
    #: Le secteur contient-il artillerie, seigneur ou heros ?
    high_value: bool
    #: Le secteur contient-il des tireurs ?
    ranged: bool
    #: Distance du secteur au centre de masse adverse.
    distance_to_mass: float
    #: Force adverse hors secteur capable de s'y retourner.
    support: float
    #: Unites que nous y envoyons.
    attackers: int
    #: Part de notre force de melee engagee dans l'assaut.
    line_committed: float

    # --- ce que l'assaut a rendu -------------------------------------------
    broke: bool
    broke_at: float | None
    #: Force adverse retiree moins force alliee perdue, sur la fenetre.
    exchange: float
    #: Meilleur rapport local encore atteignable une fois le secteur enfonce.
    #:
    #: **C'est le chiffre qui n'existait nulle part.** Il repond a « si je gagne
    #: ici, qu'est-ce que cela me permet de faire juste apres ? » — la question
    #: qui separe un calculateur d'opportunites d'un planificateur.
    followup: float | None
    outcome: str

    @property
    def profitable(self) -> bool:
        """L'echange a-t-il tourne a notre avantage ?"""
        return self.exchange > 0.0

    def explain(self) -> str:
        return (
            f"  {self.scenario:<22} g{self.seed:<3} secteur {self.sector} "
            f"rapport {self.ratio:5.2f}  {self.enemies} ennemi(s) "
            f"({self.enemy_share:4.0%} de leur armee, {self.isolated} isole(s))  "
            f"ligne engagee {self.line_committed:4.0%}  "
            f"echange {self.exchange:+5.2f}  "
            f"suite {'—' if self.followup is None else f'{self.followup:.2f}'}  "
            f"[{self.outcome}]"
        )


def describe_sector(
    state: BattleState,
    scenario: str,
    seed: int,
    sector: int,
    started_at: float,
    ratio: float,
    targets: Sequence[str],
    attackers: Sequence[str],
) -> dict[str, object]:
    """Attributs d'un secteur au moment ou l'assaut y est lance.

    Tout se calcule sur l'etat que le planificateur avait sous les yeux : aucun
    de ces champs n'est un canal privilegie, et l'agent pourrait les calculer
    lui-meme le jour ou il en aurait l'usage.
    """
    vises = set(targets)
    dedans = [unit for unit in state.enemies() if unit.id in vises and unit.is_available]
    tous = [unit for unit in state.enemies() if unit.is_available]
    engagees = [unit for unit in state.allies() if unit.id in set(attackers)]
    melee = [unit for unit in state.allies() if unit.is_available and unit.role not in RANGED_ROLES]

    force_totale = sum(unit.effective_strength for unit in tous)
    force_melee = sum(unit.effective_strength for unit in melee)
    force_secteur = sum(unit.effective_strength for unit in dedans)

    # Un ennemi « isole » n'a aucun des siens a portee de soutien : c'est
    # precisement la configuration du secteur a 4,92 de `skirmish_standoff`.
    isoles = sum(
        1
        for unit in dedans
        if not any(
            autre.id != unit.id and unit.position.distance_2d(autre.position) <= SUPPORT_RADIUS
            for autre in tous
        )
    )
    soutien = sum(
        unit.effective_strength
        for unit in tous
        if unit.id not in vises
        and any(unit.position.distance_2d(autre.position) <= SUPPORT_RADIUS for autre in dedans)
    )
    centre = centroid([unit.position for unit in dedans or tous])
    masse = centroid([unit.position for unit in tous]) if tous else centre

    return {
        "scenario": scenario,
        "seed": seed,
        "sector": sector,
        "started_at": started_at,
        "ratio": ratio,
        "enemy_share": force_secteur / force_totale if force_totale > 1e-9 else 0.0,
        "isolated": isoles,
        "enemies": len(dedans),
        "high_value": any(unit.role in HIGH_VALUE_ROLES for unit in dedans),
        "ranged": any(unit.role in RANGED_ROLES for unit in dedans),
        "distance_to_mass": centre.distance_2d(masse),
        "support": soutien,
        "attackers": len(engagees),
        "line_committed": (
            sum(unit.effective_strength for unit in engagees) / force_melee
            if force_melee > 1e-9
            else 0.0
        ),
    }


def outcome_of(
    states: Sequence[BattleState],
    targets: Sequence[str],
    attackers: Sequence[str],
    started_at: float,
    initial_enemy: float,
) -> dict[str, object]:
    """Ce que l'assaut a rendu : rupture, echange, et suite possible."""
    vises = set(targets)
    nos_ids = set(attackers)
    depart = next((item for item in states if item.game_time >= started_at), None)
    if depart is None:
        return {"broke": False, "broke_at": None, "exchange": 0.0, "followup": None}

    ennemi_depart = _strength(depart.enemies(), vises)
    allie_depart = _strength(depart.allies(), nos_ids)

    rupture: BattleState | None = None
    for etat in states:
        if etat.game_time < started_at:
            continue
        if initial_enemy > 1e-9 and _strength(etat.enemies(), vises) <= initial_enemy * BREAK_SHARE:
            rupture = etat
            break

    fin = rupture if rupture is not None else states[-1]
    retire = ennemi_depart - _strength(fin.enemies(), vises)
    perdu = allie_depart - _strength(fin.allies(), nos_ids)

    return {
        "broke": rupture is not None,
        "broke_at": rupture.game_time - started_at if rupture is not None else None,
        "exchange": retire - perdu,
        "followup": _followup(fin) if rupture is not None else None,
    }


def _strength(units: Sequence[UnitState], ids: set[str]) -> float:
    return sum(unit.effective_strength for unit in units if unit.id in ids and unit.is_available)


def _followup(state: BattleState) -> float | None:
    """Meilleur rapport local encore atteignable, une fois le secteur enfonce.

    **C'est une mesure de la situation, pas un rejeu de la decision.** Le front
    est pris de notre centroide vers le leur, sans exclure la reserve : ce que
    l'on veut savoir est ce que la position offre, pas ce que le planificateur
    aurait precisement choisi.
    """
    from totalwar_ai.agent.sectors import split_sectors

    allies = [unit for unit in state.allies() if unit.is_available]
    ennemis = [unit for unit in state.enemies() if unit.is_available]
    if not allies or not ennemis:
        return None
    depuis = centroid([unit.position for unit in allies])
    vers = centroid([unit.position for unit in ennemis])
    front = depuis.direction_to(vers)
    if front.length_2d() <= 1e-9:
        return None
    carte = split_sectors(state, front, allies)
    return max((item.local_ratio for item in carte.sectors), default=None)


@dataclass
class Study:
    """Ce que les assauts observes disent des secteurs."""

    records: list[SectorRecord] = field(default_factory=list)

    @property
    def measured(self) -> bool:
        """Y a-t-il assez de **configurations distinctes** — pas de releves.

        **La distinction est tout le sujet.** Le premier passage a produit 96
        releves, ce qui semblait large ; ils ne portaient que **quatre formes de
        secteur**, une par scenario, repetee sur douze graines et trois indices.
        Rejouer douze fois la meme configuration ne fait pas douze mesures.
        """
        return len(self.configurations) >= MINIMUM_CONFIGURATIONS

    @property
    def configurations(self) -> set[tuple[str, int, int]]:
        """Formes de secteur distinctes : scenario, nombre d'ennemis, isoles."""
        return {(item.scenario, item.enemies, item.isolated) for item in self.records}

    def confounded(self, predicate: Callable[[SectorRecord], bool]) -> bool:
        """Cet attribut ne fait-il que renommer le scenario ?

        Si aucun scenario n'apparait des deux cotes du partage, l'attribut et le
        scenario designent exactement les memes batailles, et rien ne permet de
        dire lequel des deux explique l'issue. La lecture doit alors s'abstenir
        plutot que de publier une correlation qui n'en est pas une.

        C'est le defaut que ce module a produit a son premier passage : « secteur
        valant moins d'un quart de leur armee » separait parfaitement les bons
        des mauvais echanges, et ne selectionnait rien d'autre que
        `skirmish_standoff`.
        """
        oui, non = self.split(predicate)
        if not oui or not non:
            return True
        return not ({item.scenario for item in oui} & {item.scenario for item in non})

    def split(
        self, predicate: Callable[[SectorRecord], bool]
    ) -> tuple[list[SectorRecord], list[SectorRecord]]:
        """Partage les releves selon un attribut, pour comparer les deux cotes."""
        oui = [item for item in self.records if predicate(item)]
        non = [item for item in self.records if not predicate(item)]
        return oui, non

    def render(self) -> str:
        if not self.records:
            return (
                "  Aucun assaut observe. La sonde n'a rien pu composer : verifier\n"
                "  que la fenetre glissante est active et que les secteurs existent."
            )
        lignes = [f"  {len(self.records)} assaut(s) observe(s)", ""]
        lignes += [item.explain() for item in self.records]
        lignes += ["", "--- ce qui separe un bon echange d'un mauvais ---", ""]

        for nom, predicat in (
            ("secteur contenant un ennemi isole", lambda r: r.isolated > 0),
            ("secteur valant moins d'un quart de leur armee", lambda r: r.enemy_share < 0.25),
            ("plus de la moitie de notre ligne engagee", lambda r: r.line_committed > 0.5),
            ("secteur portant artillerie / seigneur / heros", lambda r: r.high_value),
            ("secteur loin de leur centre de masse", lambda r: r.distance_to_mass > 40.0),
        ):
            oui, non = self.split(predicat)
            lignes.append(f"  {nom} :")
            lignes.append(f"    quand oui ({len(oui):>3}) : {_resume(oui)}")
            lignes.append(f"    quand non ({len(non):>3}) : {_resume(non)}")
            if self.confounded(predicat):
                lignes.append(
                    "    -> **confondu avec le scenario** : aucun scenario des deux "
                    "cotes,\n       cet attribut ne distingue rien de plus qu'un nom "
                    "de bataille."
                )

        formes = self.configurations
        lignes += [
            "",
            f"  {len(self.records)} releve(s) pour **{len(formes)} configuration(s) "
            "distincte(s)**.",
        ]
        if not self.measured:
            lignes += [
                f"  **Moins de {MINIMUM_CONFIGURATIONS} configurations : on ne conclut pas.**",
                "  Rejouer la meme forme de secteur sur douze graines ne fait pas douze",
                "  mesures : le banc doit offrir des secteurs varies avant qu'un",
                "  attribut puisse etre distingue d'un nom de scenario.",
            ]
        return "\n".join(lignes)


def _resume(records: Sequence[SectorRecord]) -> str:
    if not records:
        return "aucun releve"
    echanges = [item.exchange for item in records]
    suites = [item.followup for item in records if item.followup is not None]
    return (
        f"echange median {statistics.median(echanges):+5.2f}, "
        f"{sum(1 for item in records if item.profitable)}/{len(records)} profitables, "
        f"rupture {sum(1 for item in records if item.broke)}/{len(records)}"
        + (f", suite mediane {statistics.median(suites):.2f}" if suites else "")
    )


def probe(
    scenarios: Sequence[Scenario],
    *,
    seeds: Sequence[int],
    sectors: Sequence[int] = (0, 1, 2),
    config: AppConfig | None = None,
    on_battle: Callable[[str, int, int], None] | None = None,
) -> Study:
    """Rejoue chaque bataille en imposant chaque secteur tour a tour.

    Import local de `run_battle` et de l'agent : `simulation` depend de
    `learning`, et l'inverse ne doit exister qu'au moment de l'appel — meme
    disposition que `learning.evaluation`.

    **Ces batailles ne comptent nulle part.** Aucune memoire, aucun rapport,
    aucun taux de victoire : ce sont des experiences, pas des parties.
    """
    from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
    from totalwar_ai.config import load_config
    from totalwar_ai.simulation.runner import run_battle

    resolved = config or load_config()
    releves: list[SectorRecord] = []

    for scenario in scenarios:
        for seed in seeds:
            for secteur in sectors:
                if on_battle is not None:
                    on_battle(scenario.name, seed, secteur)
                agent = DeterministicTacticalAgent.from_config(resolved)
                agent.planner.forced_sector = secteur
                agent.planner.sliding_window = True
                resultat = run_battle(
                    scenario,
                    agent=agent,
                    config=resolved,
                    seed=seed,
                    repository=None,
                    generate_report=False,
                    keep_states=True,
                )
                etats = list(resultat.states)
                if not etats:
                    continue
                for assaut in _assaults_of(resultat.episode.events):
                    releves.append(
                        _record(assaut, etats, scenario.name, seed, resultat.outcome.value)
                    )
    return Study(records=releves)


def _assaults_of(events: Sequence[Event]) -> list[dict[str, object]]:
    """Assauts **distincts** journalises, un par manoeuvre et non par plan.

    Meme deduplication que `learning.assault` : les plans suivants reconduisent
    l'assaut tant qu'il n'a pas rompu, et les compter a chaque tour mesurerait sa
    duree, pas leur nombre.
    """
    trouves: list[dict[str, object]] = []
    precedent: tuple[object, ...] | None = None
    for event in events:
        if event.type is not EventType.PLAN_SELECTED:
            continue
        assaut = event.payload.get("assault")
        if not isinstance(assaut, dict):
            precedent = None
            continue
        cle = (assaut.get("sector"), tuple(assaut.get("targets") or ()))
        if cle != precedent:
            trouves.append(assaut)
        precedent = cle
    return trouves


def _record(
    assaut: dict[str, object],
    etats: Sequence[BattleState],
    scenario: str,
    seed: int,
    outcome: str,
) -> SectorRecord:
    debut = _number(assaut.get("started_at"))
    cibles = [str(item) for item in _sequence(assaut.get("targets"))]
    assaillants = [str(item) for item in _sequence(assaut.get("attackers"))]
    au_choix = next((item for item in etats if item.game_time >= debut), etats[0])

    attributs = describe_sector(
        au_choix,
        scenario,
        seed,
        int(_number(assaut.get("sector"), -1.0)),
        debut,
        _number(assaut.get("ratio")),
        cibles,
        assaillants,
    )
    issue = outcome_of(
        etats, cibles, assaillants, debut, _number(assaut.get("initial_enemy_strength"))
    )
    return SectorRecord(**{**attributs, **issue, "outcome": outcome})  # type: ignore[arg-type]


def _number(value: object, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()

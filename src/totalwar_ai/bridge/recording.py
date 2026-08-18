"""Enregistrement des batailles reellement pilotees dans le jeu.

**Pourquoi ceci existe.** Tout le reglage tactique s'est fait jusqu'ici contre
un simulateur ecrit ici meme. Deux corrections mesurees comme benefiques en jeu
se sont revelees nuisibles au banc, et inversement — sans qu'on puisse dire
lequel des deux juges dit vrai (voir `docs/decisions/0005`). Departager demande
des batailles reelles enregistrees dans le meme format que les simulees.

Ce que l'enregistrement retient, et ce qu'il refuse de pretendre :

* **retenu** : chaque etat observe, chaque ordre emis, chaque refus de securite,
  chaque action perdue faute de traduction, et l'evolution des effectifs ;
* **refuse** : l'issue de la bataille. Le jeu ne la dit pas — nous quittons la
  boucle avant la fin, ou l'operateur reprend la main. Une issue devinee depuis
  les forces restantes polluerait les statistiques d'apprentissage. Elle reste
  `unknown` tant que la phase `Complete` n'a pas ete observee.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.live import LiveStep
from totalwar_ai.domain.battle_state import BattleOutcomeKind
from totalwar_ai.domain.unit_state import Side
from totalwar_ai.memory.models import BattleSummary, Episode

#: Nom de scenario donne aux batailles jouees dans le jeu.
#:
#: Distinct de tout scenario simule, pour qu'une comparaison ne melange jamais
#: les deux sources : `totalwar-ai history --scenario live` les isole.
LIVE_SCENARIO = "live"

#: Marqueur en tete de chaque enregistrement de bataille reelle.
#:
#: `data/battles/` recoit aussi les journaux du simulateur, au format tout
#: different. Un fichier qui se nomme lui-meme evite au corpus d'apprentissage
#: de melanger les deux, et restera lisible dans six mois.
RECORDING_FORMAT = "totalwar_ai.live.v1"


@dataclass
class BattleRecorder:
    """Accumule ce qu'une session de pilotage produit, tour apres tour."""

    battle_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    directory: Path | None = None
    #: Conserver l'etat de **chaque unite**, a chaque tour.
    #:
    #: Sans cela, une bataille enregistree ne dit que des comptes et des sommes
    #: — dix-huit allies, quatorze de force — et rien de ce que les unites ont
    #: fait. C'est suffisant pour comparer deux issues, et totalement
    #: insuffisant pour apprendre en regardant jouer l'IA du moteur : on ne voit
    #: pas ses decisions.
    #:
    #: Cout mesure : **un mega-octet par bataille**, pour quarante unites et
    #: quatre minutes de combat — soit une trentaine de mega-octets pour le
    #: corpus d'apprentissage vise. Sans l'inventaire ecrit a part, ce serait le
    #: double : le type, le camp et la portee de tir ne changent jamais, et les
    #: repeter a chaque tour pesait plus lourd que tout le reste.
    record_units: bool = True

    #: Un objet JSON par tour, dans l'ordre.
    entries: list[dict[str, Any]] = field(default_factory=list)
    #: Inventaire des unites rencontrees : identifiant -> ce qui ne change pas.
    roster: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: Etats publies par le jeu et enregistres. Distinct de `turns`, qui compte
    #: les tours de decision : le jeu publie plus souvent qu'on ne decide.
    observations: int = 0
    orders_sent: int = 0
    orders_blocked: int = 0
    actions_lost: int = 0
    turns: int = 0

    _first_state: ProbeBattleState | None = None
    _last_state: ProbeBattleState | None = None
    #: Force initiale de chaque unite, par camp, a sa premiere apparition.
    #:
    #: Deux raisons de ne pas se contenter du premier etat. Les renforts
    #: arrivent en cours de bataille — les ignorer ferait passer une armee qui
    #: grossit pour une armee intacte. Et une unite **detruite disparait des
    #: listes du jeu** : si le denominateur ne retenait que les unites encore
    #: presentes, une armee reduite a un quart rendrait « 100 % ».
    _initial_strength: dict[str, dict[str, float]] = field(
        default_factory=lambda: {"allies": {}, "enemies": {}}
    )
    _initial_allies: int = 0
    _initial_enemies: int = 0
    _handle: Any = None

    def __post_init__(self) -> None:
        # Le fichier n'est **ouvert qu'a la premiere ecriture**. Une session qui
        # n'a jamais commence — delegation refusee par le jeu, bataille pas
        # encore lancee — laissait sinon un enregistrement vide derriere elle :
        # neuf fichiers `aucun tour enregistre` pour trois vraies batailles, au
        # premier soir de corpus.
        self.path: Path | None = (
            Path(self.directory) / f"{self.battle_id}.jsonl" if self.directory is not None else None
        )

    def _open(self) -> None:
        """Ouvre l'enregistrement, en-tete comprise. Sans effet si deja ouvert."""
        if self._handle is not None or self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("w", encoding="utf-8")
        # En-tete d'identification. Le meme repertoire recoit les journaux
        # du simulateur, au format tout different : sans cette ligne, le
        # corpus d'apprentissage les prendrait pour des batailles reelles —
        # quatre cent quinze d'un coup, constate en developpant.
        self._handle.write(
            json.dumps(
                {"format": RECORDING_FORMAT, "battle_id": self.battle_id},
                ensure_ascii=False,
            )
            + "\n"
        )

    # --- collecte ------------------------------------------------------------

    def observe(self, step: LiveStep) -> None:
        """Enregistre un tour. Un tour sans etat n'en est pas un.

        **Tous** les etats publies pendant le tour sont enregistres, pas
        seulement celui sur lequel on a decide : le jeu publie plus souvent que
        la boucle ne decide, et un etat jete ne se retrouve jamais.

        Seul l'etat de decision porte les ordres et les motifs — les autres sont
        de pures observations. `turns` continue de compter les decisions, parce
        que c'est ce que l'operateur lit ; `observations` compte les etats.
        """
        if step.state is None:
            return

        self.turns += 1
        self.orders_sent += step.sent
        self.orders_blocked += len(step.blocked)
        self.actions_lost += len(step.translation.untranslated)

        # `observed` est vide chez les appelants qui ne le remplissent pas :
        # on retombe alors sur le seul etat de decision.
        #
        # **L'instant de consommation va sur *tous* les etats, pas seulement sur
        # celui de decision.** C'est leur regroupement au meme instant mural qui
        # revele un rattrapage : quatre etats de jeu espaces d'une demi-seconde
        # tous avales au meme moment reel signalent une boucle Python qui vient
        # de se debloquer. Ne l'ecrire que sur l'etat de decision rendait cette
        # preuve-la invisible, et c'est precisement celle qui manquait.
        for state in step.observed or (step.state,):
            self._record(
                state,
                step if state is step.state else None,
                consumed_at=step.heartbeat.wall_clock,
            )

    def _record(
        self,
        state: ProbeBattleState,
        step: LiveStep | None,
        *,
        consumed_at: float = 0.0,
    ) -> None:
        """Un etat publie. `step` n'est fourni que pour l'etat de decision."""
        self._open()
        self.observations += 1
        if self._first_state is None:
            self._first_state = state
            self._initial_allies = len(state.allies)
            self._initial_enemies = len(state.enemies)
        self._last_state = state
        for camp in ("allies", "enemies"):
            connues = self._initial_strength[camp]
            for unite in getattr(state, camp):
                if unite.unit_id not in connues and unite.alive:
                    connues[unite.unit_id] = _unit_strength(unite)

        entry: dict[str, Any] = {
            "turn": self.turns,
            # Le numero de la sonde, distinct de notre compteur de tours. Sans
            # lui un trou dans le flux est indetectable, et rien ne distingue
            # une bataille exploitable d'une bataille trouee.
            "sequence": state.sequence,
            "game_time_ms": state.game_time_ms,
            "phase": state.phase,
            "allies": len(state.allies),
            "enemies": len(state.enemies),
            "ally_strength": _strength(state, "allies"),
            "enemy_strength": _strength(state, "enemies"),
        }
        # Ce que le `script_log` ne pouvait pas dire : a quel instant reel Python
        # a lu cet etat. Deux horloges valent mieux qu'une — un blocage laisse le
        # temps de jeu continu et l'horloge murale trouee.
        entry["consumed_wall_clock"] = round(consumed_at, 3)
        if step is not None:
            entry.update(
                {
                    # Marque le tour ou l'on a decide. Les autres entrees sont
                    # de pures observations : l'absence d'`orders` y est une
                    # information, pas un oubli.
                    "decision": True,
                    "skipped": step.skipped,
                    "orders": {
                        "moves": [
                            {"unit_id": unit_id, "destination": point.to_dict()}
                            for unit_id, point in step.translation.moves
                        ],
                        "attacks": [attack.to_dict() for attack in step.translation.attacks],
                        "halts": list(step.translation.halts),
                    },
                    "decisions": list(step.decisions),
                    "blocked": list(step.blocked),
                    "untranslated": [
                        {"action": action.value, "reason": reason}
                        for action, reason in step.translation.untranslated
                    ],
                    # **LIVE-001 : le diagnostic ne doit pas dependre d'un
                    # terminal reste ouvert.** Ces champs s'affichaient a l'ecran
                    # sans etre ecrits nulle part : une session parfaite, jouee
                    # exprès pour eux, les aurait perdus a la fermeture de la
                    # fenetre.
                    "decision_due": step.heartbeat.decision_due,
                    "no_command_stage": step.no_command_stage,
                    "stages": dict(step.stages),
                    "suppressed": step.suppressed,
                    "sent": step.sent,
                    "acknowledgement": step.acknowledgement.to_dict(),
                    "planner_reasons": dict(step.planner_reasons),
                }
            )
            # Ce que nous aurions fait, l'IA du moteur menant la bataille. C'est
            # la matiere premiere de l'apprentissage par observation : chaque
            # tour devient un couple etiquete, sans jouer une bataille de plus.
            if step.shadow is not None:
                entry["shadow"] = step.shadow.to_dict()
        if self.record_units:
            self._refresh_roster(state)
            entry["units"] = [
                _unit_entry(observation)
                for groupe in (state.allies, state.enemies)
                for observation in groupe
            ]
        self.entries.append(entry)
        if self._handle is not None:
            self._handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._handle.flush()

    def _refresh_roster(self, state: ProbeBattleState) -> None:
        """Publie l'inventaire des unites, et le republie si de nouvelles arrivent.

        Une bataille de campagne peut recevoir des renforts en cours de route :
        un inventaire ecrit une fois pour toutes laisserait ces unites sans
        type ni camp, donc inexploitables.
        """
        nouvelles = {
            observation.unit_id: _roster_entry(observation, camp)
            for camp, groupe in (("ally", state.allies), ("enemy", state.enemies))
            for observation in groupe
            if observation.unit_id not in self.roster
        }
        if not nouvelles:
            return
        self.roster.update(nouvelles)
        if self._handle is not None:
            ligne = {"turn": self.turns, "roster": nouvelles}
            self._handle.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    # --- restitution ---------------------------------------------------------

    @property
    def outcome(self) -> BattleOutcomeKind:
        """Issue de la bataille — `unknown` sauf si le jeu l'a annoncee.

        Deviner depuis les forces restantes reviendrait a inventer une donnee
        d'apprentissage. Une session interrompue par l'operateur, ou une armee
        en bonne posture au moment ou l'on cesse d'observer, ne sont pas des
        victoires.
        """
        if self._last_state is None or self._last_state.phase != "Complete":
            return BattleOutcomeKind.UNKNOWN

        # **Seules les unites vivantes comptent** — precaution, non correction
        # d'un defaut constate. La premiere bataille menee a son terme a bien
        # renvoye `victory` : a la phase `Complete`, le camp vaincu ne figurait
        # plus du tout dans les listes du jeu. Rien ne garantit qu'il en aille
        # toujours ainsi, et une unite detruite mais encore listee ferait
        # basculer un aneantissement en match nul.
        allies = [unite for unite in self._last_state.allies if unite.alive]
        enemies = [unite for unite in self._last_state.enemies if unite.alive]
        if not enemies and allies:
            return BattleOutcomeKind.VICTORY
        if not allies and enemies:
            return BattleOutcomeKind.DEFEAT

        # **Une armee entierement en deroute a perdu.** Ce n'est pas une
        # supposition tiree des forces restantes : c'est l'etat que le jeu
        # publie a la phase `Complete`. Constate sur les trois premieres
        # batailles reelles — neuf unites alliees toutes en deroute, quatorze
        # unites adverses dont aucune, et l'issue annoncee « match nul » quand
        # l'operateur avait vu trois defaites a l'ecran.
        nos_deroutes = allies and all(unite.routing or unite.shattered for unite in allies)
        leurs_deroutes = enemies and all(unite.routing or unite.shattered for unite in enemies)
        if nos_deroutes and not leurs_deroutes:
            return BattleOutcomeKind.DEFEAT
        if leurs_deroutes and not nos_deroutes:
            return BattleOutcomeKind.VICTORY
        return BattleOutcomeKind.DRAW

    def summary(self) -> BattleSummary:
        """Fiche comparable a celle d'une bataille simulee."""
        debut = self._first_state.game_time_ms if self._first_state else 0
        fin = self._last_state.game_time_ms if self._last_state else 0
        return BattleSummary(
            battle_id=self.battle_id,
            scenario=LIVE_SCENARIO,
            outcome=self.outcome,
            duration=max(0.0, (fin - debut) / 1000.0),
            ally_remaining=_final_share(
                self._last_state, "allies", self._initial_strength["allies"]
            ),
            enemy_remaining=_final_share(
                self._last_state, "enemies", self._initial_strength["enemies"]
            ),
            actions_sent=self.orders_sent,
            actions_blocked=self.orders_blocked,
            agent_mode="deterministic-live",
            army_fingerprint=_fingerprint(self._first_state),
            created_at=time.time(),
            metrics={
                "turns": self.turns,
                "actions_lost": self.actions_lost,
                "source": "game",
                "initial_allies": self._initial_allies,
                "initial_enemies": self._initial_enemies,
                "final_phase": self._last_state.phase if self._last_state else "",
            },
        )

    def episode(self) -> Episode:
        """Episode enregistrable en memoire.

        **Sans transitions ni recompenses.** Le calcul de recompense repose sur
        des evenements que le jeu ne fournit pas — qui a tire sur qui, quelles
        pertes a produit quel ordre. Les fabriquer ferait entrer du bruit dans
        la memoire d'apprentissage sous les traits d'une mesure.
        """
        return Episode(summary=self.summary())


#: Precision des coordonnees enregistrees, en decimales.
#:
#: Le metre suffit largement pour retrouver qui allait vers qui, et trois
#: decimales par unite et par tour multipliees par trente batailles font du
#: volume pour rien.
_POSITION_PRECISION = 1


def _roster_entry(observation: ProbeUnitObservation, side: str) -> dict[str, Any]:
    """Ce qui ne change pas d'un tour a l'autre : ecrit une fois, pas deux cents.

    Repeter le type, le camp et la portee de tir a chaque tour multipliait par
    trois le poids d'une bataille — deux mega-octets et demi la ou huit cents
    kilo-octets suffisent, et soixante-dix mega-octets pour un corpus de trente.
    """
    entry: dict[str, Any] = {"side": side, "type": observation.unit_type}
    if observation.commanding:
        entry["commanding"] = True
    if observation.can_fly:
        entry["can_fly"] = True
    if observation.missile_range is not None:
        entry["missile_range"] = observation.missile_range
    return entry


def _unit_entry(observation: ProbeUnitObservation) -> dict[str, Any]:
    """Ce qui bouge : position et condition, tour par tour.

    **Un champ absent veut dire que le jeu ne l'expose pas**, et jamais zero —
    c'est la meme regle que dans le protocole. Un moral a zero se confondrait
    avec une unite qui rompt ; une munition a zero, avec un carquois vide.
    """
    entry: dict[str, Any] = {
        "id": observation.unit_id,
        "x": round(observation.position.x, _POSITION_PRECISION),
        # **L'altitude est la seule donnee de terrain que le jeu nous donne.**
        # `position():get_y()` repond — verifie en bataille, entre 21 et 33 — et
        # elle etait jetee. Elle dit tout de suite qui tient la hauteur, et,
        # accumulee sur des dizaines de batailles, elle dessine le relief des
        # cartes deja jouees.
        "y": round(observation.position.y, _POSITION_PRECISION),
        "z": round(observation.position.z, _POSITION_PRECISION),
    }
    # Les booleens ne sont ecrits que lorsqu'ils sont vrais : sur une bataille
    # entiere, la plupart des unites ne sont ni au contact, ni en deroute, ni
    # cachees, et quatre `false` par ligne pesent plus que l'information.
    if not observation.alive:
        entry["dead"] = True
    if observation.in_melee:
        entry["in_melee"] = True
    if observation.idle:
        entry["idle"] = True
    if observation.routing or observation.shattered:
        entry["routing"] = True
    if observation.hidden:
        entry["hidden"] = True
    # **Ecrit quand il est FAUX**, a rebours des autres : `targetable` vaut vrai
    # la quasi-totalite du temps, et c'est son absence qui porte l'information.
    #
    # Sans lui, le corpus ne voit pas ce que le jeu refusait. Trois ennemis ont
    # ete declares non ciblables 665, 644 et 644 fois sur deux batailles : une
    # unite lancee sur l'un d'eux recoit un refus par seconde et ne fait rien.
    # `Planner.can_be_attacked` s'appuie dessus en direct — un corpus qui l'ignore
    # apprend d'une bataille ou tout etait attaquable, ce qui n'a jamais ete vrai.
    if not observation.targetable:
        entry["untargetable"] = True
    # Meme convention, et meme raison. La relecture **deduisait** ce drapeau du
    # camp — « allie donc controlable » —, ce qui est faux precisement quand cela
    # compte : en supervision, nos propres unites sont confiees a l'IA du jeu et
    # cessent de l'etre. Aucun module d'apprentissage ne le lit aujourd'hui, mais
    # une valeur inventee dans un corpus finit toujours par etre lue un jour.
    if not observation.controllable:
        entry["uncontrollable"] = True
    # **Ecrit quand il est FAUX**, a rebours des autres : `targetable` vaut vrai
    # la quasi-totalite du temps, et c'est son absence qui porte l'information.
    #
    # Sans lui, le corpus ne voit pas ce que le jeu refusait. Trois ennemis ont
    # ete declares non ciblables 665, 644 et 644 fois sur deux batailles : une
    # unite lancee sur l'un d'eux recoit un refus par seconde et ne fait rien.
    # `Planner.can_be_attacked` s'appuie dessus en direct — un corpus qui l'ignore
    # apprend d'une bataille ou tout etait attaquable, ce qui n'a jamais ete vrai.
    # **Ecrit quand il est FAUX**, a rebours des autres : `targetable` vaut vrai
    # la quasi-totalite du temps, et c'est son absence qui porte l'information.
    #
    # Sans lui, le corpus ne voit pas ce que le jeu refusait. Trois ennemis ont
    # ete declares non ciblables 665, 644 et 644 fois sur deux batailles : une
    # unite lancee sur l'un d'eux recoit un refus par seconde et ne fait rien.
    # `Planner.can_be_attacked` s'appuie dessus en direct — un corpus qui l'ignore
    # apprend d'une bataille ou tout etait attaquable, ce qui n'a jamais ete vrai.
    if observation.hitpoints is not None:
        entry["hitpoints"] = round(observation.hitpoints, 3)
    if observation.men_alive is not None:
        entry["men_alive"] = observation.men_alive
    if observation.bearing is not None:
        entry["bearing"] = round(observation.bearing, 1)
    if observation.ammo is not None:
        entry["ammo"] = observation.ammo
    return entry


def _strength(state: ProbeBattleState, side: str) -> float:
    """Somme des sante des unites vivantes d'un camp, ou leur nombre a defaut."""
    unites = [unite for unite in getattr(state, side) if unite.alive]
    if not unites:
        return 0.0
    connues = [unite.hitpoints for unite in unites if unite.hitpoints is not None]
    return sum(connues) if connues else float(len(unites))


def _unit_strength(unite: ProbeUnitObservation) -> float:
    """Force d'une unite : ses hommes debout, ponderes par leur sante."""
    hommes = unite.men_alive if unite.men_alive is not None else 1
    sante = unite.hitpoints if unite.hitpoints is not None else 1.0
    return max(0.0, float(hommes) * float(sante))


def _final_share(state: ProbeBattleState | None, side: str, initial: Mapping[str, float]) -> float:
    """Part d'un camp **encore au combat**, dans [0, 1].

    Deux corrections a la version qui comptait les unites vivantes.

    **Une unite en deroute ne compte plus.** Elle a des hommes debout, donc elle
    etait comptee intacte : une bataille ou les douze unites alliees ont rompu
    rendait « 100 % de forces restantes ». Le chiffre par lequel le projet se
    juge annoncait une armee entiere la ou il ne restait personne au combat.

    **On mesure une force, pas un compte d'unites.** La simulation somme des
    points de vie ; compter des unites ici rendait les deux chiffres etrangers
    l'un a l'autre alors que tout le projet les compare.
    """
    if state is None or not initial:
        return 0.0
    # Le denominateur porte sur **toutes** les unites vues, pas sur celles qui
    # restent : le jeu retire une unite detruite de ses listes, et la compter
    # seulement si elle est encore la rendrait toute perte invisible.
    depart = sum(initial.values())
    if depart <= 0.0:
        return 0.0
    reste = sum(
        _unit_strength(unite)
        for unite in getattr(state, side)
        if unite.alive and not unite.routing and not unite.shattered
    )
    return min(1.0, reste / depart)


def _fingerprint(state: ProbeBattleState | None) -> str:
    """Empreinte de composition, pour retrouver les batailles semblables.

    **Elle ne comptait que les unites**, et rendait `allies:12|enemies:10`. Deux
    armees entierement differentes — douze lanciers contre douze chevaliers —
    portaient donc la meme empreinte, et `MemoryRepository.find_similar` les
    donnait pour comparables. L'adaptation de doctrine demarrant des la deuxieme
    bataille, il suffisait de deux affrontements sans rapport pour qu'elle tire
    une lecon de leur moyenne.

    Elle compte desormais les **roles**, exactement comme
    `simulation.scenarios.Scenario.fingerprint` : c'est la condition pour que les
    batailles jouees et simulees soient comparables au lieu d'etre melangees sous
    des empreintes de natures differentes.
    """
    if state is None:
        return ""
    from totalwar_ai.agent.unit_classifier import UnitClassifier

    classifieur = UnitClassifier.from_config()
    # Le meme classifieur qu'en direct, sur les memes entrees : une empreinte
    # calculee autrement ici qu'en bataille ne retrouverait jamais ses pareilles.
    domaine = classifieur.classify_state(state.to_battle_state("empreinte"))
    parts: list[str] = []
    for camp, side in (("ally", Side.ALLY), ("enemy", Side.ENEMY)):
        comptes: dict[str, int] = {}
        for unite in domaine.side_units(side):
            comptes[unite.role.value] = comptes.get(unite.role.value, 0) + 1
        detail = ",".join(f"{role}x{nombre}" for role, nombre in sorted(comptes.items()))
        parts.append(f"{camp}:{detail}")
    return "|".join(parts)

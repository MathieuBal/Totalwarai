"""Agent tactique deterministe.

Assemble les briques : classification -> plan -> ordres -> securite. Il gere
aussi les trois frequences de decision decrites dans le README (surveillance
critique, tactique locale, plan general) et evite de re-emettre un ordre deja
actif.

C'est le mode de secours permanent du projet : quoi qu'apporte l'apprentissage
plus tard, cet agent doit rester utilisable seul.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from totalwar_ai.agent.doctrine import apply_to_planner, apply_to_safety
from totalwar_ai.agent.explainability import Decision
from totalwar_ai.agent.grouping import GroupKind
from totalwar_ai.agent.planner import (
    ABSTAIN_UNKNOWN,
    BattlePlan,
    Planner,
    PlannerSettings,
)
from totalwar_ai.agent.safety_rules import SafetyEngine, SafetySettings
from totalwar_ai.agent.unit_classifier import UnitClassifier
from totalwar_ai.config import AppConfig, ConfigError, load_config
from totalwar_ai.domain.actions import AgentAction
from totalwar_ai.domain.battle_state import BattlePhase, BattleState
from totalwar_ai.domain.unit_state import PRECIOUS_ROLES, RANGED_ROLES
from totalwar_ai.learning.adaptation import DoctrineProfile

if TYPE_CHECKING:
    from totalwar_ai.learning.targeting import TargetingModel


#: Etages de la chaine de decision, dans l'ordre ou une commande peut y mourir.
#:
#: **Nommer l'etage est tout l'objet de LIVE-001.** En bataille reelle, l'agent
#: est reste 364 secondes sans emettre une seule commande pendant que son armee
#: passait de 12 a 9 unites. Le journal du jeu prouve le silence ; il ne dit pas
#: ou, dans la chaine, la commande a disparu. Sans ce champ, chercher la cause
#: revient a deviner.
STAGE_PLANNER = "planner"
STAGE_CONFIDENCE = "confidence"
STAGE_SAFETY = "safety"
STAGE_DUPLICATES = "duplicate_suppression"
STAGE_THROTTLE = "throttle"
#: Deux etages qui n'existent que dans le pont, et que le banc ne traverse jamais.
#:
#: Le banc appelle l'agent directement : ni traduction en ordres du jeu, ni filtre
#: de micro-deplacements. Une paralysie logee la serait donc **structurellement
#: invisible** au banc, quel que soit le nombre de scenarios.
STAGE_TRANSLATION = "translation"
STAGE_MICRO_MOVE = "micro_move"

#: Les compteurs ne correspondent a aucun chemin connu.
#:
#: **Un instrument qui ne comprend pas doit le dire.** Le repli precedent
#: retombait sur `safety` quand aucune condition n'expliquait le zero — soit
#: « je ne comprends pas, ce doit etre la securite », l'inverse exact de ce que
#: les six dernieres corrections ont etabli. Un diagnostic invente coute plus
#: cher qu'un diagnostic absent : il envoie chercher au mauvais endroit.
STAGE_INVARIANT = "INVARIANT_VIOLATION"


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Ce que l'agent a decide pour un etat donne."""

    sequence: int
    game_time: float
    plan: BattlePlan | None = None
    decisions: tuple[Decision, ...] = ()
    blocked: tuple[Decision, ...] = ()
    suppressed: int = 0
    skipped_reason: str | None = None
    #: Une decision etait-elle **reellement due** a ce tour ?
    #:
    #: **La cadence nominale n'est pas une panne.** L'agent ne decide qu'une fois
    #: par `decision_interval` ; compter chaque releve intermediaire comme une
    #: abstention ferait de « cadence » l'etage responsable de tout, et
    #: designerait le fonctionnement normal comme coupable.
    decision_due: bool = False
    #: Comptes par etage, du planificateur a l'emission.
    proposed: int = 0
    below_confidence: int = 0
    #: Entrees et **sorties** de la securite, et non ses seuls refus.
    #:
    #: **`SafetyEngine.filter` peut bloquer un ordre et en produire un autre.**
    #: Une charge suicidaire refusee devient un `HOLD_POSITION`, remis dans
    #: `allowed`. Compter les refus revenait donc a lire ceci :
    #:
    #: .. code-block:: text
    #:
    #:     5 propositions, 5 originales bloquees, 5 remplacements produits,
    #:     5 remplacements tues ensuite par l'anti-repetition
    #:     -> NO_COMMAND stage=safety
    #:
    #: alors que la securite avait fourni cinq ordres parfaitement utilisables et
    #: que le tuyau s'est vide plus loin. Seule la **sortie** dit si un etage a
    #: vide le tuyau.
    safety_input: int = 0
    safety_blocked_originals: int = 0
    safety_replacements: int = 0
    safety_output: int = 0
    duplicates: int = 0
    throttled: int = 0
    #: Motifs de renoncement du planificateur, comptes par code stable.
    #:
    #: Vide quand le planificateur a propose quelque chose. Un motif inconnu
    #: reste `UNKNOWN` : jamais deduit apres coup.
    planner_reasons: tuple[tuple[str, int], ...] = ()

    @property
    def emitted(self) -> int:
        return len(self.decisions)

    @property
    def counters(self) -> dict[str, int]:
        """Tous les comptes, pour accompagner le verdict — surtout s'il est faux."""
        return {
            "proposed": self.proposed,
            "below_confidence": self.below_confidence,
            "safety_input": self.safety_input,
            "safety_blocked_originals": self.safety_blocked_originals,
            "safety_replacements": self.safety_replacements,
            "safety_output": self.safety_output,
            "duplicates": self.duplicates,
            "throttled": self.throttled,
            "emitted": self.emitted,
        }

    @property
    def no_command_stage(self) -> str | None:
        """Ou la commande a disparu, quand une decision etait due et rien n'est sorti.

        `None` quand une commande est sortie, ou quand aucune decision n'etait
        due : dans ce dernier cas il n'y a rien a expliquer.

        Chaque etage n'est accuse que s'il a **vide** le tuyau : entree non nulle,
        sortie nulle. Et quand aucun chemin connu n'explique le zero, le verdict
        est `INVARIANT_VIOLATION` — jamais un etage choisi par defaut.
        """
        if not self.decision_due or self.emitted:
            return None
        if self.proposed == 0:
            return STAGE_PLANNER
        if self.below_confidence >= self.proposed:
            return STAGE_CONFIDENCE
        if self.safety_input > 0 and self.safety_output == 0:
            return STAGE_SAFETY
        if self.safety_output > 0 and self.duplicates >= self.safety_output:
            return STAGE_DUPLICATES
        if self.throttled > 0:
            return STAGE_THROTTLE
        return STAGE_INVARIANT

    @property
    def actions(self) -> tuple[AgentAction, ...]:
        return tuple(decision.action for decision in self.decisions)

    @property
    def is_idle(self) -> bool:
        return not self.decisions and not self.blocked

    def explanations(self) -> list[str]:
        return [decision.explain() for decision in (*self.decisions, *self.blocked)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "game_time": self.game_time,
            "plan": self.plan.to_dict() if self.plan else None,
            "decisions": [decision.to_dict() for decision in self.decisions],
            "blocked": [decision.to_dict() for decision in self.blocked],
            "suppressed": self.suppressed,
            "skipped_reason": self.skipped_reason,
            "decision_due": self.decision_due,
            "no_command_stage": self.no_command_stage,
            "planner_reasons": dict(self.planner_reasons),
            **self.counters,
        }


def _abstentions(reasons: dict[str, int], *, proposed: int) -> tuple[tuple[str, int], ...]:
    """Motifs de renoncement, avec `UNKNOWN` quand rien n'a ete propose ni nomme.

    **`UNKNOWN` est un resultat, pas un echec.** Le planificateur n'a rien
    propose et aucune branche n'a dit pourquoi : le dire franchement vaut mieux
    que de reconstruire une explication en relisant l'etat, qui produirait une
    cause plausible plutot que la vraie.
    """
    nommes = tuple(sorted(reasons.items()))
    if nommes or proposed:
        return nommes
    return ((ABSTAIN_UNKNOWN, 1),)


def default_safety_engine() -> SafetyEngine:
    """Moteur de securite avec l'ensemble des regles par defaut activees."""
    return SafetyEngine.from_settings(SafetySettings())


@dataclass
class DeterministicTacticalAgent:
    """Agent a base de regles, sans aucun modele appris."""

    planner: Planner = field(default_factory=Planner)
    safety: SafetyEngine = field(default_factory=default_safety_engine)
    classifier: UnitClassifier = field(default_factory=UnitClassifier.from_config)
    decision_interval: float = 2.0
    strategic_interval: float = 10.0
    confidence_threshold: float = 0.55

    battle_id: str | None = None
    plan: BattlePlan | None = None
    doctrine: DoctrineProfile = field(default_factory=DoctrineProfile)
    _last_decision_time: float | None = None
    #: Duree pendant laquelle un ordre deja envoye vaut encore, en secondes.
    #:
    #: **Sans expiration, l'armee s'immobilise.** Le jeu rend la main sur chaque
    #: unite au bout de cinq secondes ; celle-ci s'arrete alors ou elle se
    #: trouve, sans avoir atteint son point. Le planificateur repropose le meme
    #: deplacement, qui est ecarte comme doublon — et l'unite ne repart jamais.
    #:
    #: Mesure en bataille : `51eed1bc`, douze deplacements a t=3 s puis **trente
    #: ordres en sept cents secondes**, l'operateur ayant du jouer a la place de
    #: l'agent. Un ordre repete est un defaut de journal ; une armee qui ne bouge
    #: plus est une bataille perdue.
    #:
    #: Six secondes : un peu plus que la fenetre du jeu, pour ne pas renvoyer un
    #: ordre encore en cours d'execution.
    order_ttl: float = 6.0
    _active_signatures: dict[str, tuple[Any, ...]] = field(default_factory=dict, repr=False)
    #: Instant du dernier envoi de chaque signature, pour la faire expirer.
    _signature_time: dict[str, float] = field(default_factory=dict, repr=False)
    _blocked_signatures: set[tuple[Any, ...]] = field(default_factory=set, repr=False)

    @classmethod
    def from_config(cls, config: AppConfig | None = None) -> DeterministicTacticalAgent:
        """Construit l'agent depuis la configuration applicative."""
        resolved = config or load_config()
        agent_cfg = resolved.agent
        safety_cfg = resolved.safety
        return cls(
            planner=Planner(
                settings=PlannerSettings.from_config(agent_cfg, safety_cfg),
                targeting=_learned_targeting(resolved),
            ),
            safety=SafetyEngine.from_config(safety_cfg),
            classifier=UnitClassifier.from_config(),
            decision_interval=float(agent_cfg.get("decision_interval_seconds", 2.0)),
            strategic_interval=float(agent_cfg.get("strategic_interval_seconds", 10.0)),
            confidence_threshold=float(agent_cfg.get("confidence_threshold", 0.55)),
        )

    # --- cycle de vie --------------------------------------------------------

    def reset(self, battle_id: str | None = None) -> None:
        """Prepare l'agent pour une nouvelle bataille.

        Un arret d'urgence en cours survit a cette reinitialisation : reprendre
        la main est une decision du joueur, pas un effet de bord du cycle de vie.
        """
        self.battle_id = battle_id
        self.plan = None
        self._last_decision_time = None
        self._active_signatures.clear()
        self._signature_time.clear()
        self._blocked_signatures.clear()
        # Le planificateur porte lui aussi de la memoire — engagements de cible,
        # composition de la reserve, detection d'enlisement. La laisser passer
        # d'une bataille a la suivante ferait dependre la seconde de la premiere,
        # et deux batailles identiques n'auraient pas le meme deroulement.
        self.planner.reset()
        self.safety.reset()

    def apply_doctrine(self, profile: DoctrineProfile) -> None:
        """Applique une doctrine apprise de l'historique.

        Le profil ne touche qu'a des reglages bornes (voir
        :mod:`totalwar_ai.learning.adaptation`) : les interdits de securite ne
        sont jamais assouplis par ce chemin.
        """
        if profile.is_empty:
            return
        # `forced_posture` doit survivre au rechargement de doctrine : la
        # perdre ici rendrait l'ordre de l'operateur silencieusement caduc.
        self.planner = Planner(
            settings=apply_to_planner(self.planner.settings, profile),
            forced_posture=self.planner.forced_posture,
            # Le modele appris survit lui aussi : le perdre ici rendrait
            # l'apprentissage silencieusement caduc des la premiere doctrine
            # rechargee, et rien ne le signalerait.
            targeting=self.planner.targeting,
        )
        self.safety = SafetyEngine.from_settings(apply_to_safety(self.safety.settings, profile))
        self.doctrine = profile

    def trigger_emergency_stop(self) -> None:
        """Arret d'urgence : le joueur reprend la main immediatement."""
        self.safety.trigger_emergency_stop()

    def release_emergency_stop(self) -> None:
        self.safety.release_emergency_stop()

    @property
    def emergency_stopped(self) -> bool:
        return self.safety.emergency_stop

    # --- decision ------------------------------------------------------------

    def decide(self, raw_state: BattleState) -> AgentTurn:
        """Produit les ordres pour l'etat courant."""
        state = self.classifier.classify_state(raw_state)
        if self.battle_id != state.battle_id:
            self.reset(state.battle_id)

        if state.phase is BattlePhase.FINISHED:
            return AgentTurn(
                sequence=state.sequence,
                game_time=state.game_time,
                plan=self.plan,
                skipped_reason="bataille terminee",
            )

        if not self._should_decide(state):
            return AgentTurn(
                sequence=state.sequence,
                game_time=state.game_time,
                plan=self.plan,
                skipped_reason="cadence tactique non atteinte",
            )

        self._refresh_plan(state)
        plan = self.plan
        assert plan is not None  # garanti par _refresh_plan

        # **Chaque etage est compte separement.** Une commande peut mourir a six
        # endroits, et le journal du jeu ne montre que le silence qui en resulte.
        # Sans ces comptes, nommer le responsable du trou de 364 secondes revient
        # a deviner.
        brutes = self.planner.decisions_for(state, plan)
        proposals = [
            decision for decision in brutes if decision.confidence >= self.confidence_threshold
        ]
        rear = plan.anchor - plan.front_direction.scaled(self.planner.settings.reserve_offset)
        outcome = self.safety.filter(proposals, state, rear=rear)

        # Ordre volontaire : securite -> anti-repetition -> limite de debit.
        # Le budget d'ordres par minute ne doit jamais etre consomme par des
        # ordres redondants ou par des actions deja refusees.
        kept, suppressed = self._drop_duplicates(outcome.allowed, state.game_time)
        throttled = self.safety.throttle(kept, state.game_time)
        blocked, repeated = self._drop_repeated_blocks(outcome.blocked)

        self._last_decision_time = state.game_time
        return AgentTurn(
            sequence=state.sequence,
            game_time=state.game_time,
            plan=plan,
            decisions=throttled.allowed,
            blocked=(*blocked, *throttled.blocked),
            suppressed=suppressed + repeated,
            decision_due=True,
            proposed=len(brutes),
            below_confidence=len(brutes) - len(proposals),
            safety_input=len(proposals),
            safety_blocked_originals=len(outcome.blocked),
            # Une decision bloquee porte son remplacement : le compter ici evite
            # de toucher au moteur de securite pour une mesure.
            safety_replacements=sum(
                1 for decision in outcome.blocked if decision.replacement is not None
            ),
            safety_output=len(outcome.allowed),
            duplicates=suppressed,
            throttled=len(throttled.blocked),
            # **Le motif vient de la branche qui renonce**, jamais d'une lecture
            # de l'etat apres coup. `UNKNOWN` quand le planificateur n'a rien
            # propose sans avoir nomme sa raison : un trou honnete vaut mieux
            # qu'une explication plausible.
            planner_reasons=_abstentions(self.planner.abstentions, proposed=len(brutes)),
        )

    # --- cadence -------------------------------------------------------------

    def _should_decide(self, state: BattleState) -> bool:
        """Trois frequences : deploiement, urgence, cadence tactique."""
        if state.phase is BattlePhase.DEPLOYMENT:
            return True
        if self._last_decision_time is None:
            return True
        if state.game_time - self._last_decision_time >= self.decision_interval:
            return True
        return self._has_critical_situation(state)

    def _has_critical_situation(self, state: BattleState) -> bool:
        """Surveillance haute frequence : tireurs menaces, commandement en peril."""
        radius = self.planner.settings.ranged_threat_radius
        for unit in state.allies():
            if unit.role in RANGED_ROLES and (unit.is_engaged or state.threats_to(unit, radius)):
                return True
            if unit.role in PRECIOUS_ROLES and unit.health_ratio < 0.4 and unit.is_engaged:
                return True
        return False

    def _refresh_plan(self, state: BattleState) -> None:
        """Recalcule le plan general a basse frequence, ou si la situation a change."""
        plan = self.plan
        needs_refresh = (
            plan is None
            or state.phase is BattlePhase.DEPLOYMENT
            or state.game_time - plan.created_at >= self.strategic_interval
            or self._plan_is_stale(plan, state)
        )
        if needs_refresh:
            self.plan = self.planner.build_plan(state)

    def _plan_is_stale(self, plan: BattlePlan, state: BattleState) -> bool:
        """Un plan devient caduc quand ses groupes ont perdu des unites."""
        for group in plan.groups.non_empty():
            if group.kind is GroupKind.RESERVE:
                continue
            if len(group.available_units(state)) != len(group.unit_ids):
                return True
        return False

    # --- anti-repetition -----------------------------------------------------

    def _drop_duplicates(
        self, decisions: Sequence[Decision], game_time: float
    ) -> tuple[list[Decision], int]:
        """Supprime les ordres identiques a ceux **encore en cours**.

        Re-envoyer sans cesse le meme ordre est penalise (`unnecessary_order_change`)
        et brouille la lecture des journaux.

        **Mais un ordre ne reste pas actif indefiniment.** Le jeu rend la main sur
        chaque unite au bout de quelques secondes, et elle s'arrete alors sans
        avoir atteint son point. Une signature retenue pour toujours transformait
        cette anti-repetition en paralysie : le planificateur reproposait le bon
        deplacement a chaque cycle, et il etait ecarte a chaque fois.
        """
        kept: list[Decision] = []
        suppressed = 0
        for decision in decisions:
            signature = decision.action.signature()
            key = ",".join(sorted(decision.action.actor_ids))
            depuis = game_time - self._signature_time.get(key, float("-inf"))
            if self._active_signatures.get(key) == signature and depuis < self.order_ttl:
                suppressed += 1
                continue
            self._active_signatures[key] = signature
            self._signature_time[key] = game_time
            kept.append(decision)
        return kept, suppressed

    def _drop_repeated_blocks(self, decisions: Sequence[Decision]) -> tuple[list[Decision], int]:
        """Ne signale un refus qu'une fois tant que la situation ne change pas.

        Le planificateur reproposera la meme action a chaque cycle ; la journaliser
        des centaines de fois noierait le rapport sans rien apprendre de plus.
        """
        kept: list[Decision] = []
        repeated = 0
        for decision in decisions:
            signature = (decision.blocked_by, decision.action.signature())
            if signature in self._blocked_signatures:
                repeated += 1
                continue
            self._blocked_signatures.add(signature)
            kept.append(decision)
        return kept, repeated


def _learned_targeting(config: AppConfig) -> TargetingModel | None:
    """Ce que l'IA du jeu recherche, si on l'a deja appris d'elle.

    **L'absence de modele n'est pas une erreur** : c'est l'etat par defaut du
    projet, et l'agent doit pouvoir jouer sans corpus. Un fichier illisible est
    traite comme une absence, pour la meme raison — un modele est une aide,
    jamais une dependance.
    """
    from totalwar_ai.learning.targeting import TargetingModel

    try:
        chemin = config.path("memory", "models_dir") / "targeting.json"
    except ConfigError:
        return None
    if not chemin.exists():
        return None
    modele = TargetingModel.load(chemin)
    return modele if modele.affinities else None

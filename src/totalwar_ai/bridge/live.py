"""Boucle de pilotage : l'agent decide, le jeu execute.

C'est le raccord des quatre morceaux construits separement — observation de la
bataille, memoire des effectifs, agent tactique, commande de groupe. Chaque tour
suit toujours le meme enchainement :

    lire l'etat -> memoriser les effectifs -> traduire vers le domaine
    -> decider -> traduire en ordres -> publier

**Ce que cette boucle refuse de faire**, et qui compte autant que ce qu'elle
fait :

* elle n'emet rien avant que la bataille ne soit engagee — un ordre donne en
  deploiement est acquitte par le moteur mais ne produit aucun deplacement ;
* elle n'emet rien si l'arret d'urgence est demande ;
* elle ne traduit pas approximativement une action sans equivalent : elle la
  compte et la nomme.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.command_models import ProbeBattleState, ProbeUnitObservation
from totalwar_ai.bridge.file_bridge import FileBridge
from totalwar_ai.bridge.orders import OrderTranslator, Translation
from totalwar_ai.bridge.roster import RosterMemory
from totalwar_ai.bridge.supervision import Intervention, Supervisor
from totalwar_ai.domain.actions import ActionType
from totalwar_ai.domain.battle_state import BattleState
from totalwar_ai.domain.geometry import Vector3

LOGGER = logging.getLogger("totalwar_ai.bridge.live")

#: Distance en deca de laquelle un nouvel ordre de deplacement est superflu.
#:
#: Constate en bataille : le seigneur faisait des allers-retours. Sa position de
#: soutien se calcule par rapport au barycentre de l'armee, dont il fait partie
#: — il bouge, le barycentre bouge, sa cible bouge, il rebouge. La deduplication
#: de l'agent ne l'attrape pas : la destination change de quelques metres a
#: chaque fois, donc l'ordre n'est jamais tout a fait le meme.
#:
#: Vingt metres : en deca, le deplacement ne vaut ni l'ordre ni la saccade.
MIN_REORDER_DISTANCE = 20.0


@dataclass(frozen=True, slots=True)
class LiveStep:
    """Compte rendu d'un tour de boucle, lisible tel quel par l'operateur."""

    #: `None` quand aucun etat n'etait disponible.
    state: ProbeBattleState | None = None
    #: Raison pour laquelle rien n'a ete emis, le cas echeant.
    skipped: str | None = None
    #: Decisions **retenues** par l'agent, expliquees.
    decisions: tuple[str, ...] = ()
    #: Decisions **refusees** par les regles de securite, avec leur motif.
    #:
    #: Distinguees des precedentes : les afficher ensemble laissait croire que
    #: neuf actions avaient ete prises la ou deux ordres seulement etaient
    #: partis, les sept autres ayant ete bloquees.
    blocked: tuple[str, ...] = ()
    translation: Translation = field(default_factory=Translation)
    #: Unites reprises a l'IA du jeu ce tour-ci, avec leur motif.
    interventions: tuple[Intervention, ...] = ()
    #: Unites rendues a l'IA du jeu ce tour-ci.
    returned: tuple[str, ...] = ()
    #: **Tous** les etats publies par le jeu pendant ce tour, le dernier etant
    #: `state`. La boucle decide sur le plus recent, mais l'enregistrement les
    #: garde tous : la frequence d'observation n'est pas celle de decision, et
    #: un etat jete est une donnee d'apprentissage perdue pour toujours.
    observed: tuple[ProbeBattleState, ...] = ()
    #: Ce que notre agent aurait decide, sans que rien ne parte vers le jeu.
    shadow: ShadowDecision | None = None
    #: Ordres que l'agent a tus parce qu'il les jugeait deja en cours.
    #:
    #: **Se dit, sinon la paralysie est muette.** Une armee immobilisee par
    #: l'anti-repetition affichait « rien a faire » tour apres tour, exactement
    #: comme une armee qui n'a effectivement rien a faire — l'operateur a joue
    #: sept cents secondes a la place de l'agent sans qu'aucune ligne ne le
    #: signale.
    suppressed: int = 0
    #: Unites que le jeu a **refuse** de rendre ou de reprendre, avec le motif.
    #:
    #: Une supervision qui ne lit pas les accuses croit avoir agi : constate en
    #: bataille, la meme unite reprise quatre fois parce que chaque reprise
    #: etait rejetee sans que rien ne le remarque.
    refused: tuple[tuple[str, str], ...] = ()
    sent: int = 0

    @property
    def acted(self) -> bool:
        return self.sent > 0

    def summary(self) -> str:
        """Une ligne, dans la langue de l'operateur."""
        if self.state is None:
            return "aucun etat recu du jeu"
        entete = (
            f"t={self.state.game_time_ms / 1000:6.1f}s "
            f"{len(self.state.allies):2d} allies / {len(self.state.enemies):2d} ennemis"
        )
        if self.skipped:
            return f"{entete} — {self.skipped}"

        detail = []
        if self.translation.moves:
            detail.append(f"{len(self.translation.moves)} deplacement(s)")
        if self.translation.attacks:
            detail.append(f"{len(self.translation.attacks)} attaque(s)")
        if self.translation.halts:
            detail.append(f"{len(self.translation.halts)} arret(s)")

        # Les actions perdues se disent **toujours**, y compris quand d'autres
        # sont parties. Ne les montrer qu'en l'absence d'ordre les a fait passer
        # inapercues en bataille : deux deplacements masquaient trois
        # contournements abandonnes, et le compte rendu paraissait normal.
        if self.translation.untranslated:
            noms = ", ".join(sorted({item[0].value for item in self.translation.untranslated}))
            detail.append(f"{len(self.translation.untranslated)} action(s) perdue(s) : {noms}")

        # Un refus de securite est une decision, pas un silence : il doit se
        # voir au meme titre qu'un ordre emis.
        if self.blocked:
            detail.append(f"{len(self.blocked)} refusee(s) par la securite")
        if self.interventions:
            detail.append(f"{len(self.interventions)} reprise(s) a l'IA du jeu")
        if self.returned:
            detail.append(f"{len(self.returned)} rendue(s) a l'IA du jeu")
        if self.refused:
            detail.append(f"{len(self.refused)} refusee(s) par le jeu")
        if self.suppressed:
            detail.append(f"{self.suppressed} deja en cours")

        return f"{entete} — " + (", ".join(detail) if detail else "rien a faire")


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """Ce que **nous** aurions fait, sans rien envoyer au jeu.

    L'IA du moteur mene la bataille ; notre agent decide en parallele, dans le
    vide. Chaque tour devient alors un couple etiquete — « elle a fait ceci,
    nous aurions fait cela » — et c'est la matiere premiere de l'apprentissage
    par observation, obtenue sans jouer une bataille de plus.

    Deuxieme usage, tout aussi utile : les regles de supervision sont evaluees
    elles aussi, et l'on sait enfin **a quelle frequence chacune se
    declencherait en vraie bataille**. `artillerie_au_contact` n'a jamais rien
    declenche au banc ; cela dira si c'est le banc ou la regle.

    **Rien de tout ceci ne part vers le jeu.** C'est une observation, et la
    garantie tient par construction : ni l'agent ni la traduction ne touchent au
    pont.
    """

    decisions: tuple[str, ...] = ()
    blocked: tuple[str, ...] = ()
    translation: Translation = field(default_factory=Translation)
    #: `(identifiant, regle)` pour chaque reprise qu'un superviseur aurait faite.
    rules: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "decisions": list(self.decisions),
            "blocked": list(self.blocked),
            "moves": [
                {"unit_id": unit_id, "destination": point.to_dict()}
                for unit_id, point in self.translation.moves
            ],
            "attacks": [attack.to_dict() for attack in self.translation.attacks],
            "halts": list(self.translation.halts),
            "untranslated": [
                {"action": action.value, "reason": reason}
                for action, reason in self.translation.untranslated
            ],
            "rules": [{"unit_id": unit_id, "rule": rule} for unit_id, rule in self.rules],
        }


@dataclass
class LiveSession:
    """Pilote une bataille en cours, un tour a la fois.

    La session ne dort jamais et ne boucle pas d'elle-meme : `step()` fait un
    tour et rend la main. L'appelant garde ainsi le controle de la cadence, et
    la boucle reste testable sans horloge.
    """

    bridge: FileBridge
    agent: DeterministicTacticalAgent
    roster: RosterMemory = field(default_factory=RosterMemory)
    translator: OrderTranslator = field(default_factory=OrderTranslator)
    battle_id: str = "live"
    #: Duree au bout de laquelle le jeu rend la main sur chaque unite prise.
    #:
    #: Volontairement courte : le joueur doit pouvoir reprendre une unite sans
    #: attendre, et un ordre perime vaut mieux qu'une unite confisquee.
    release_after_ms: int = 5000
    #: Delai d'attente de l'accuse, en secondes.
    #:
    #: Court : savoir qu'un ordre a ete refuse vaut la peine, pas au prix d'un
    #: tour de boucle perdu a l'attendre.
    ack_timeout: float = 0.5
    wait: Callable[[float], None] = time.sleep
    #: Derniere destination envoyee a chaque unite, pour ne pas la faire osciller.
    _last_destination: dict[str, Vector3] = field(default_factory=dict)

    def step(self) -> LiveStep:
        """Un tour complet. Ne leve pas : un tour rate ne doit pas tout arreter."""
        states = self.bridge.read_battle_states()
        if not states:
            return LiveStep()
        # On decide sur le dernier etat, mais on rend compte de **tous** ceux
        # publies depuis le tour precedent : voir coute moins cher qu'agir, et
        # un etat jete ne se retrouve jamais.
        return replace(self._decide(states[-1]), observed=tuple(states))

    def _decide(self, state: ProbeBattleState) -> LiveStep:
        """Le tour proprement dit, sur l'etat le plus recent."""
        self.roster.observe(state)

        if self.bridge.stop_requested:
            return LiveStep(state=state, skipped="arret d'urgence demande")
        if self.agent.emergency_stopped:
            return LiveStep(state=state, skipped="agent en arret d'urgence")
        if not state.orders_take_effect:
            return LiveStep(
                state=state,
                skipped=f"phase {state.phase} : un ordre n'aurait aucun effet",
            )

        domaine = state.to_battle_state(
            self.battle_id,
            entity_ratios=self._ratios(state, self.roster.entity_ratio),
            ammo_ratios=self._ratios(state, self.roster.ammo_ratio),
        )
        tour = self.agent.decide(domaine)
        if tour.skipped_reason:
            return LiveStep(state=state, skipped=tour.skipped_reason)

        translation = self.translator.translate(tour.actions, domaine)
        for action_type, motif in translation.untranslated:
            LOGGER.debug("action non traduite : %s (%s)", action_type.value, motif)
        translation = self._drop_micro_moves(translation, domaine)

        prises = tuple(decision.explain() for decision in tour.decisions)
        refusees = tuple(decision.explain() for decision in tour.blocked)

        if translation.is_empty:
            return LiveStep(
                state=state,
                decisions=prises,
                blocked=refusees,
                translation=translation,
                suppressed=tour.suppressed,
            )

        # Un seul message : deux commandes successives se perdraient, le
        # fichier etant remplace et relu toutes les 500 ms seulement.
        commande = self.bridge.send_orders(
            translation.moves,
            translation.attacks,
            translation.halts,
            release_after_ms=self.release_after_ms,
        )
        return LiveStep(
            state=state,
            decisions=prises,
            blocked=refusees,
            translation=translation,
            sent=translation.order_count,
            suppressed=tour.suppressed,
            refused=self._refusals(commande.sequence),
        )

    def _refusals(self, sequence: int) -> tuple[tuple[str, str], ...]:
        """Ce que le jeu a refuse d'executer, et pourquoi.

        **Ce mode ne lisait aucun accuse.** Un ordre refuse — cible devenue non
        ciblable, groupe verrouille, unite reprise a la souris — ne laissait
        aucune trace : le compte rendu annoncait « 3 attaque(s) » pour trois
        ordres tombes dans le vide, et l'unite restait plantee sans que rien ne
        le dise. Le Lua ne renvoie que le premier motif, ce qui suffit a savoir
        qu'il faut regarder.

        Ne bloque jamais la boucle : un accuse en retard vaut mieux qu'un tour
        perdu a l'attendre.
        """
        ack = self.bridge.wait_for_ack(sequence, timeout=self.ack_timeout, sleep=self.wait)
        if ack is None or (ack.accepted and not ack.error):
            return ()
        motif = ack.error or ack.status.value
        return tuple((unit_id, motif) for unit_id in ack.refused_ids) or (("", motif),)

    def _drop_micro_moves(self, translation: Translation, state: BattleState) -> Translation:
        """Ecarte les deplacements trop courts pour valoir un ordre.

        Sans cela une unite dont la destination se recalcule a chaque tour
        oscille sur place — constate en jeu sur le seigneur, qui faisait des
        allers-retours. Une destination reellement nouvelle passe ; une
        correction de quelques metres est ignoree, et la memoire n'est pas
        mise a jour, pour que la derive lente finisse par franchir le seuil.

        **Mais une unite qui n'est pas arrivee doit etre relancee.** Le jeu rend
        la main au bout de `release_after_ms` — cinq secondes — et l'unite
        s'arrete alors ou elle se trouve. Comme l'agent recalculait la meme
        destination, il la jugeait deja envoyee et ne la renvoyait jamais :
        l'armee restait plantee. Bataille `a1274d62` : douze deplacements a
        t=3 s, puis **cent quatre-vingt-dix secondes sans un ordre**, jusqu'a ce
        que l'operateur deplace une unite a la souris — ce qui decalait sa
        position et faisait enfin franchir le seuil a la destination recalculee.

        La memoire ne sert donc qu'a taire les corrections **d'une unite deja
        arrivee**. Tant qu'elle est loin de son point, on repete l'ordre.
        """
        gardes: list[tuple[str, Vector3]] = []
        for unit_id, point in translation.moves:
            precedente = self._last_destination.get(unit_id)
            if precedente is not None and precedente.distance_2d(point) < MIN_REORDER_DISTANCE:
                unite = state.unit(unit_id)
                arrivee = (
                    unite is not None
                    and unite.position.distance_2d(precedente) < MIN_REORDER_DISTANCE
                )
                if arrivee:
                    LOGGER.debug("deplacement ignore pour %s : deja sur place", unit_id)
                    continue
                LOGGER.debug("deplacement repete pour %s : pas encore arrivee", unit_id)
            self._last_destination[unit_id] = point
            gardes.append((unit_id, point))

        # Une unite qu'on arrete ou qu'on lance a l'attaque n'a plus de
        # destination en cours : son prochain deplacement doit repartir libre.
        for unit_id in (*translation.halts, *(item.unit_id for item in translation.attacks)):
            self._last_destination.pop(unit_id, None)

        if len(gardes) == len(translation.moves):
            return translation
        return Translation(
            moves=tuple(gardes),
            attacks=translation.attacks,
            halts=translation.halts,
            untranslated=translation.untranslated,
        )

    def stop(self) -> None:
        """Arret d'urgence : le jeu libere tout et le joueur reprend la main."""
        self.agent.trigger_emergency_stop()
        self.bridge.abort("arret demande par la boucle de pilotage")

    @staticmethod
    def _ratios(
        state: ProbeBattleState,
        mesure: Callable[[ProbeUnitObservation], float | None],
    ) -> dict[str, float]:
        """Applique une mesure de `RosterMemory` a toutes les unites vues.

        Les unites pour lesquelles la mesure ne conclut pas sont **absentes** du
        resultat, et non presentes a zero : l'appelant retombe alors sur le
        defaut du domaine plutot que sur un chiffre fabrique.
        """
        resultats: dict[str, float] = {}
        for observation in (*state.allies, *state.enemies):
            valeur = mesure(observation)
            if valeur is not None:
                resultats[observation.unit_id] = valeur
        return resultats


#: Actions que la boucle sait rendre aujourd'hui.
#:
#: Seule `REORIENT_FRONT` reste hors de portee : elle demande un ordre
#: d'orientation que le jeu n'expose pas, et elle avait ete retiree du perimetre
#: de l'agent apres mesure (voir ADR 0004).
TRANSLATABLE_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.MOVE_GROUP,
        ActionType.RETREAT,
        ActionType.DISENGAGE,
        ActionType.FORM_RESERVE,
        ActionType.HOLD_POSITION,
        ActionType.ATTACK_TARGET,
        ActionType.FOCUS_FIRE,
        ActionType.CHASE_ROUTING,
        ActionType.FLANK,
        ActionType.PROTECT,
    }
)


@dataclass
class SupervisedSession:
    """L'IA du jeu mene la bataille ; nos regles corrigent ses angles morts.

    Elle connait le terrain, le pathfinding et les formations — nous non. Lui
    confier l'armee est donc le chemin le plus court vers un mod qui joue bien.
    Mais elle a des angles morts etablis : AI General 3 consacre l'essentiel de
    son code a les contourner.

    Le cycle de chaque tour :

        observer -> reprendre les unites mal employees -> les corriger
        -> rendre celles dont la situation est retablie

    **Rien n'est repris sans motif**, et le motif est journalise dans la meme
    forme que les decisions de l'agent. Une session qui n'interviendrait jamais
    doit se lire aussi clairement qu'une session qui intervient souvent.
    """

    bridge: FileBridge
    roster: RosterMemory = field(default_factory=RosterMemory)
    supervisor: Supervisor = field(default_factory=Supervisor)
    battle_id: str = "live"
    #: Unites actuellement confiees a l'IA du jeu.
    delegated: set[str] = field(default_factory=set)
    #: Duree de prise en main d'une unite reprise, en millisecondes.
    #:
    #: Nettement plus long que pour un ordre ordinaire : une unite reprise doit
    #: avoir le temps de se degager avant que le jeu ne la rende au joueur.
    release_after_ms: int = 20_000
    #: Delai d'attente d'un accuse, en secondes.
    #:
    #: Le Lua relit le fichier de commande toutes les 500 ms ; deux secondes
    #: laissent la marge d'un cycle manque sans figer la boucle.
    ack_timeout: float = 2.0
    #: Comment patienter entre deux lectures d'accuse.
    #:
    #: Le jeu continue de tourner pendant l'attente ; un banc d'essai, lui, doit
    #: faire avancer sa fausse sonde. Injecter l'attente est ce qui permet de
    #: tester la boucle fermee sans horloge reelle.
    wait: Callable[[float], None] = time.sleep
    #: Unites que le jeu refuse d'atteindre, et l'instant ou les redemander.
    #:
    #: **Le refus est temporaire, et le croire definitif coute des unites.**
    #: Constate en bataille : quatre unites de tir reprises puis rendues ont ete
    #: refusees d'affilee — « unite non controlable » — parce qu'elles etaient
    #: en deroute a cet instant. Ecartees pour de bon, elles ont fini la bataille
    #: sans pilote, ni a nous ni a l'IA du jeu. Une unite qui rompt peut se
    #: rallier ; il faut lui laisser cette chance.
    #:
    #: Insister a chaque tour reste exclu : cela produirait un ordre refuse par
    #: seconde. D'ou le delai.
    unreachable: dict[str, float] = field(default_factory=dict)
    #: Delai avant de redemander une unite refusee, en secondes de jeu.
    retry_after: float = 30.0
    #: Agent qui decide **dans le vide**, pour comparaison. Voir `ShadowDecision`.
    shadow_agent: DeterministicTacticalAgent | None = None
    #: Superviseur d'ombre : quelles regles se seraient declenchees ?
    #:
    #: Distinct de `supervisor`, qui agit vraiment. Celui-ci ne sert qu'a
    #: mesurer, et son etat interne n'influence jamais la bataille.
    shadow_rules: Supervisor | None = None
    translator: OrderTranslator = field(default_factory=OrderTranslator)
    #: Pourquoi la derniere delegation a echoue, dans les mots du jeu.
    #:
    #: **Un echec sans motif n'est pas diagnosticable.** Le Lua nomme la cause —
    #: unite introuvable, non controlable, `script_ai_planner` impossible — et
    #: nous la jetions : l'operateur ne lisait que « le jeu a refuse », ce qui ne
    #: distingue pas un refus d'un accuse jamais recu.
    last_refusal: str = ""

    def delegate_all(self, state: ProbeBattleState) -> list[str]:
        """Confie a l'IA du jeu toutes les unites controlables.

        **Le compte rendu est celui du jeu, pas le notre.** Annoncer dix-huit
        unites confiees quand le Lua n'en a pris que six s'est produit en
        bataille : l'accuse portait `accepted` et le vrai compte dans son
        detail, que personne ne lisait.
        """
        self.last_refusal = ""
        unit_ids = [unite.unit_id for unite in state.allies if unite.controllable and unite.alive]
        if not unit_ids:
            self.last_refusal = "aucune unite controlable et vivante dans l'etat recu"
            return []
        commande = self.bridge.delegate(unit_ids)
        ack = self.bridge.wait_for_ack(commande.sequence, timeout=self.ack_timeout, sleep=self.wait)
        if ack is None:
            # Silence et refus ne se soignent pas pareil : l'un dit que le Lua
            # n'a pas repondu a temps, l'autre qu'il a repondu non.
            self.last_refusal = (
                f"aucun accuse en {self.ack_timeout:.0f} s "
                f"(sequence {commande.sequence}) : la sonde repond-elle ?"
            )
            self.delegated = set()
            return []
        if not ack.accepted:
            self.last_refusal = ack.error or f"refus sans motif (statut {ack.status.value})"
            self.delegated = set()
            return []
        self._reject(ack.refused_ids, 0.0)
        confiees = [unit_id for unit_id in unit_ids if unit_id not in self.unreachable]
        self.delegated = set(confiees)
        return confiees

    def step(self) -> LiveStep:
        """Un tour de surveillance. Ne leve pas."""
        states = self.bridge.read_battle_states()
        if not states:
            return LiveStep()
        # L'attente d'un accuse peut durer deux secondes, pendant lesquelles le
        # jeu publie plusieurs etats. Les jeter etait une perte silencieuse.
        return replace(self._surveille(states[-1]), observed=tuple(states))

    def _reject(self, unit_ids: Iterable[str], now: float) -> list[str]:
        """Ecarte des unites pour un temps, et rend celles qui viennent de l'etre."""
        nouvelles = [unit_id for unit_id in unit_ids if unit_id not in self.unreachable]
        for unit_id in nouvelles:
            self.unreachable[unit_id] = now + self.retry_after
        return nouvelles

    def _forget_expired(self, now: float) -> None:
        """Rend leur chance aux unites ecartees il y a assez longtemps."""
        for unit_id in [uid for uid, quand in self.unreachable.items() if now >= quand]:
            del self.unreachable[unit_id]

    def _surveille(self, state: ProbeBattleState) -> LiveStep:
        """La surveillance proprement dite, sur l'etat le plus recent."""
        self.roster.observe(state)
        maintenant = state.game_time_ms / 1000.0
        self._forget_expired(maintenant)
        if self.bridge.stop_requested:
            return LiveStep(state=state, skipped="arret d'urgence demande")

        domaine = state.to_battle_state(
            self.battle_id,
            entity_ratios=self._ratios(state, self.roster.entity_ratio),
            ammo_ratios=self._ratios(state, self.roster.ammo_ratio),
        )
        domaine = _classified(domaine)
        ombre = self._shadow(domaine)

        interventions = self.supervisor.review(domaine, self.delegated)
        rendues = self.supervisor.ready_to_return(domaine)
        refuses: list[tuple[str, str]] = []

        if interventions:
            # Reprise partielle : l'IA du jeu continue de mener le reste.
            demandees = [item.unit_id for item in interventions]
            refuses += self._confirm(
                self.bridge.reclaim(demandees).sequence, demandees, "reprise", maintenant
            )
            interventions = [item for item in interventions if item.unit_id not in self.unreachable]
            for intervention in interventions:
                self.delegated.discard(intervention.unit_id)
            moves = [
                (item.unit_id, item.destination)
                for item in interventions
                if item.destination is not None
            ]
            if moves:
                self.bridge.send_orders(moves, release_after_ms=self.release_after_ms)

        if rendues:
            refuses += self._confirm(
                self.bridge.delegate(rendues).sequence, rendues, "restitution", maintenant
            )
            rendues = [unit_id for unit_id in rendues if unit_id not in self.unreachable]
            self.delegated.update(rendues)
            self.supervisor.forget(rendues)

        adoptees = self._adopt(state, exclues={item.unit_id for item in interventions})
        if adoptees:
            rendues = [*rendues, *adoptees]

        return LiveStep(
            state=state,
            decisions=tuple(item.explain() for item in interventions),
            interventions=tuple(interventions),
            returned=tuple(rendues),
            refused=tuple(refuses),
            shadow=ombre,
            sent=len(interventions) + len(rendues),
        )

    def _adopt(self, state: ProbeBattleState, *, exclues: set[str]) -> list[str]:
        """Confie a l'IA du jeu les unites qui lui echappent encore.

        **Une delegation faite une fois au depart ne couvre pas une bataille.**
        Constate au premier soir de corpus : neuf unites confiees sur douze
        allies, et l'operateur voyant son armee « pousser, mais pas avec toutes
        les unites ». Une unite peut n'etre pas encore controlable au moment ou
        l'on delegue, arriver en renfort, ou etre relachee par le planificateur
        du jeu — dans les trois cas elle reste plantee jusqu'a la fin.

        Ne touche pas aux unites que la supervision vient de reprendre : elles
        sont a nous **volontairement**, et les reconfier annulerait la correction
        dans le tour meme ou elle est donnee.
        """
        candidates = [
            unite.unit_id
            for unite in state.allies
            if unite.controllable
            and unite.alive
            and unite.unit_id not in self.delegated
            and unite.unit_id not in self.unreachable
            and unite.unit_id not in exclues
            and unite.unit_id not in self.supervisor.held
        ]
        if not candidates:
            return []
        refuses = self._confirm(
            self.bridge.delegate(candidates).sequence,
            candidates,
            "adoption",
            state.game_time_ms / 1000.0,
        )
        adoptees = [unit_id for unit_id in candidates if unit_id not in self.unreachable]
        self.delegated.update(adoptees)
        # Les refus alimentent `unreachable` dans `_confirm` : on ne redemandera
        # pas la meme unite a chaque tour jusqu'a la fin de la bataille.
        del refuses
        return adoptees

    def _shadow(self, domaine: BattleState) -> ShadowDecision | None:
        """Ce que nous aurions fait, calcule sans rien envoyer au jeu.

        Ni l'agent ni la traduction ne touchent au pont : la garantie de ne rien
        emettre tient par construction, et un test la verifie en comptant les
        commandes publiees pendant une session d'observation.
        """
        if self.shadow_agent is None and self.shadow_rules is None:
            return None

        decisions: tuple[str, ...] = ()
        refusees: tuple[str, ...] = ()
        traduction = Translation()
        if self.shadow_agent is not None:
            tour = self.shadow_agent.decide(domaine)
            decisions = tuple(item.explain() for item in tour.decisions)
            refusees = tuple(item.explain() for item in tour.blocked)
            traduction = self.translator.translate(tour.actions, domaine)

        regles: tuple[tuple[str, str], ...] = ()
        if self.shadow_rules is not None:
            # Le perimetre est l'armee entiere : on veut savoir ce qui se serait
            # declenche, pas ce que la supervision reelle a le droit de toucher.
            perimetre = {unite.id for unite in domaine.allies()}
            regles = tuple(
                (item.unit_id, item.rule) for item in self.shadow_rules.review(domaine, perimetre)
            )
            # Rendre aussitot : ce superviseur ne tient aucune unite, et le
            # laisser accumuler l'empecherait de se declencher a nouveau.
            self.shadow_rules.forget([unit_id for unit_id, _ in regles])

        return ShadowDecision(
            decisions=decisions, blocked=refusees, translation=traduction, rules=regles
        )

    def _confirm(
        self, sequence: int, demandees: Sequence[str], quoi: str, now: float = 0.0
    ) -> list[tuple[str, str]]:
        """Attend l'accuse et retient les unites que le jeu refuse d'atteindre.

        **Sans cette confirmation la supervision tourne a vide.** Constate en
        bataille : chaque reprise etait rejetee par « unite introuvable », rien
        ne le lisait, la regle se redeclenchait au tour suivant — vingt-trois
        interventions, aucune appliquee, la meme unite reprise quatre fois.

        Une unite refusee est ecartee **pour un temps** : insister a chaque tour
        produirait un ordre refuse par seconde, mais l'ecarter pour de bon a deja
        coute quatre unites de tir sur une bataille entiere.
        """
        ack = self.bridge.wait_for_ack(sequence, timeout=self.ack_timeout, sleep=self.wait)
        if ack is None:
            perdues = self._reject(demandees, now)
            self.supervisor.forget(list(perdues))
            return [(item, f"{quoi} sans accuse du jeu") for item in perdues]

        motif = ack.error or f"{quoi} refusee par le jeu"
        # Un accuse peut etre accepte **et** partiel : la liste prime sur le statut.
        rejetees = list(ack.refused_ids) if ack.accepted else list(demandees)
        nouvelles = self._reject(rejetees, now)
        self.supervisor.forget(list(nouvelles))
        for unit_id in nouvelles:
            self.delegated.discard(unit_id)
        return [(item, motif) for item in nouvelles]

    def stop(self) -> None:
        """Arret d'urgence : le jeu libere tout et le joueur reprend la main."""
        self.bridge.abort("arret demande par la supervision")
        self.delegated.clear()

    @staticmethod
    def _ratios(
        state: ProbeBattleState,
        mesure: Callable[[ProbeUnitObservation], float | None],
    ) -> dict[str, float]:
        return LiveSession._ratios(state, mesure)


def _classified(state: BattleState) -> BattleState:
    """Etat dont les roles sont deduits, sans quoi aucune regle ne s'applique.

    La supervision raisonne par role — artillerie, tireurs, seigneur — et
    l'observation brute du jeu n'en fournit aucun.
    """
    from totalwar_ai.agent.unit_classifier import UnitClassifier

    return UnitClassifier.from_config().classify_state(state)

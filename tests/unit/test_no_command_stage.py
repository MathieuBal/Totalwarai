"""Ou la commande a disparu — et l'instrument qui refuse de l'inventer.

En bataille reelle, l'agent est reste 364 secondes sans emettre une commande. Le
journal du jeu prouve le silence ; nommer l'etage est ce qui permettra de le
corriger sans deviner. Ces tests pincent les deux facons dont ce diagnostic
pourrait mentir : accuser le mauvais etage, ou en inventer un.
"""

from __future__ import annotations

from totalwar_ai.agent.planner import ABSTAIN_UNKNOWN
from totalwar_ai.agent.tactical_agent import (
    STAGE_CONFIDENCE,
    STAGE_DUPLICATES,
    STAGE_INVARIANT,
    STAGE_PLANNER,
    STAGE_SAFETY,
    STAGE_THROTTLE,
    AgentTurn,
    _abstentions,
)


def _turn(**champs: object) -> AgentTurn:
    base: dict[str, object] = {"sequence": 1, "game_time": 10.0, "decision_due": True}
    base.update(champs)
    return AgentTurn(**base)  # type: ignore[arg-type]


# --- l'etage n'est accuse que s'il a vide le tuyau ----------------------------


def test_la_securite_n_est_pas_accusee_quand_elle_a_produit_des_remplacements() -> None:
    """Le defaut que ce contrat ferme.

    `SafetyEngine.filter` peut bloquer une charge suicidaire **et** produire un
    `HOLD_POSITION` a la place, remis dans `allowed`. Compter les refus faisait
    donc lire « stage=safety » alors que la securite avait fourni cinq ordres
    parfaitement utilisables, tues plus loin par l'anti-repetition.
    """
    tour = _turn(
        proposed=5,
        safety_input=5,
        safety_blocked_originals=5,
        safety_replacements=5,
        safety_output=5,
        duplicates=5,
    )
    assert tour.no_command_stage == STAGE_DUPLICATES
    assert tour.no_command_stage != STAGE_SAFETY


def test_la_securite_est_accusee_quand_elle_vide_reellement_le_tuyau() -> None:
    """Le garde-fou doit pouvoir dire oui, sinon il ne dit rien."""
    tour = _turn(proposed=5, safety_input=5, safety_blocked_originals=5, safety_output=0)
    assert tour.no_command_stage == STAGE_SAFETY


def test_un_planificateur_muet_est_designe_avant_tout_le_reste() -> None:
    assert _turn(proposed=0).no_command_stage == STAGE_PLANNER


def test_la_confiance_est_designee_quand_elle_ecarte_tout() -> None:
    tour = _turn(proposed=4, below_confidence=4)
    assert tour.no_command_stage == STAGE_CONFIDENCE


def test_la_limite_de_debit_est_designee_quand_elle_seule_a_mordu() -> None:
    tour = _turn(proposed=3, safety_input=3, safety_output=3, duplicates=0, throttled=3)
    assert tour.no_command_stage == STAGE_THROTTLE


# --- l'instrument n'invente pas ----------------------------------------------


def test_des_compteurs_incoherents_donnent_une_violation_d_invariant() -> None:
    """« Je ne comprends pas, ce doit etre la securite » est un diagnostic invente.

    Le repli precedent retombait sur `safety` quand aucun chemin connu
    n'expliquait le zero. Un diagnostic invente coute plus cher qu'un diagnostic
    absent : il envoie chercher au mauvais endroit.
    """
    tour = _turn(proposed=3, safety_input=3, safety_output=3, duplicates=0, throttled=0)
    assert tour.no_command_stage == STAGE_INVARIANT
    assert tour.counters["safety_output"] == 3, "les compteurs accompagnent le verdict"


# --- la cadence nominale n'est pas une panne ---------------------------------


def test_un_tour_sans_decision_due_ne_designe_aucun_etage() -> None:
    """L'agent ne decide qu'une fois par cadence : les autres tours ne sont rien."""
    assert AgentTurn(sequence=1, game_time=1.0, decision_due=False).no_command_stage is None


def test_un_tour_qui_commande_ne_designe_aucun_etage() -> None:
    """Sinon le champ se remplirait a chaque tour et ne designerait plus rien."""
    from totalwar_ai.agent.safety_rules import Decision
    from totalwar_ai.domain.actions import ActionType, AgentAction

    decision = Decision(
        action=AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a",)),
        cause="essai",
        objective="essai",
    )
    assert _turn(proposed=1, decisions=(decision,)).no_command_stage is None


# --- les motifs du planificateur ---------------------------------------------


def test_un_renoncement_sans_motif_reste_inconnu() -> None:
    """`UNKNOWN` est un resultat, pas un echec.

    Reconstruire la cause en relisant l'etat apres coup produirait une
    explication plausible plutot que la vraie.
    """
    assert _abstentions({}, proposed=0) == ((ABSTAIN_UNKNOWN, 1),)


def test_un_motif_nomme_prime_sur_l_inconnu() -> None:
    assert _abstentions({"NO_FEASIBLE_SECTOR": 3}, proposed=0) == (("NO_FEASIBLE_SECTOR", 3),)


def test_aucun_motif_n_est_publie_quand_le_planificateur_a_propose() -> None:
    """Un motif de renoncement pour un tour qui a propose designerait un silence
    qui n'a pas eu lieu."""
    assert _abstentions({}, proposed=5) == ()


# --- l'accuse du jeu : trois issues, et non deux ------------------------------


class _PontFactice:
    """Pont reduit a ce que `_acknowledgement` lui demande."""

    def __init__(self, ack: object) -> None:
        self._ack = ack

    def wait_for_ack(self, sequence: int, *, timeout: float, sleep: object) -> object:
        return self._ack


def _accuse(ack: object, sent: int = 4) -> object:
    from totalwar_ai.bridge.live import LiveSession

    session = LiveSession.__new__(LiveSession)
    session.bridge = _PontFactice(ack)  # type: ignore[assignment]
    session.ack_timeout = 0.1
    session.wait = 0.01  # type: ignore[assignment]
    return session._acknowledgement(1, sent)[0]


def test_un_accuse_absent_n_est_pas_un_accuse_accepte() -> None:
    """Les deux rendaient un tuple de refus vide, donc se lisaient pareil.

    « Python a ecrit quatre ordres » et « le Lua n'a jamais vu le fichier »
    produisaient le meme compte rendu, et l'instrument annoncait que tout allait
    bien.
    """
    absent = _accuse(None)
    assert absent.ack_timeout is True  # type: ignore[attr-defined]
    assert absent.sent_by_python == 4  # type: ignore[attr-defined]
    assert absent.acknowledged_by_lua == 0  # type: ignore[attr-defined]


def test_un_accuse_accepte_est_compte_comme_recu() -> None:
    from totalwar_ai.bridge.command_models import ProbeAck, ProbeStatus

    recu = _accuse(ProbeAck(sequence=1, status=ProbeStatus.ACCEPTED))
    assert recu.ack_timeout is False  # type: ignore[attr-defined]
    assert recu.acknowledged_by_lua == 4  # type: ignore[attr-defined]
    assert recu.refused_by_lua == 0  # type: ignore[attr-defined]


def test_un_refus_du_jeu_se_distingue_des_deux_autres() -> None:
    from totalwar_ai.bridge.command_models import ProbeAck, ProbeStatus

    refuse = _accuse(
        ProbeAck(sequence=1, status=ProbeStatus.ACCEPTED, error="1007 : unite non controlable"),
        sent=4,
    )
    assert refuse.ack_timeout is False  # type: ignore[attr-defined]
    assert refuse.refused_by_lua >= 1  # type: ignore[attr-defined]

"""Boucle complete etat -> decision -> resultat a travers le pont factice."""

from __future__ import annotations

import pytest

from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.bridge.mock_bridge import MockBridge, always_reject
from totalwar_ai.bridge.protocol import (
    PROTOCOL_VERSION,
    ActionResultMessage,
    BattleStateMessage,
    decode_message,
)
from totalwar_ai.domain.actions import ActionResult, ActionStatus
from totalwar_ai.domain.battle_state import BattlePhase
from totalwar_ai.domain.serialization import SchemaError
from totalwar_ai.simulation.environment import SimulationEnvironment
from totalwar_ai.simulation.scenarios import get_scenario


def _states(count: int = 12) -> list:  # type: ignore[type-arg]
    scenario = get_scenario("balanced_clash")
    environment = SimulationEnvironment(
        "bridge-test", scenario.units, seed=scenario.seed, max_battle_seconds=200.0
    )
    states = [environment.state()]
    for _ in range(count):
        step = environment.step()
        states.append(step.state)
        if step.finished:
            break
    return states


def test_boucle_etat_decision_resultat(agent: DeterministicTacticalAgent) -> None:
    bridge = MockBridge.from_states(_states())
    sent = 0
    while (state := bridge.receive_state()) is not None:
        turn = agent.decide(state)
        bridge.send_actions(turn.actions)
        results = bridge.poll_results()
        assert len(results) == len(turn.actions)
        assert all(result.accepted for result in results)
        sent += len(turn.actions)

    assert sent > 0
    assert len(bridge.sent_actions) == sent
    assert bridge.remaining_states == 0


def test_messages_envoyes_respectent_le_protocole(agent: DeterministicTacticalAgent) -> None:
    bridge = MockBridge.from_states(_states(4))
    while (state := bridge.receive_state()) is not None:
        bridge.send_actions(agent.decide(state).actions)

    assert bridge.sent_messages
    for payload in bridge.sent_messages:
        assert payload["protocol_version"] == PROTOCOL_VERSION
        decoded = decode_message(payload)
        assert decoded.message_type.value == "agent_actions"


def test_refus_de_l_adaptateur_est_remonte(agent: DeterministicTacticalAgent) -> None:
    bridge = MockBridge.from_states(_states(3), result_policy=always_reject("unite introuvable"))
    rejected = 0
    while (state := bridge.receive_state()) is not None:
        bridge.send_actions(agent.decide(state).actions)
        for result in bridge.poll_results():
            if not result.accepted:
                rejected += 1
                assert result.error == "unite introuvable"
    assert rejected > 0


def test_construction_depuis_des_messages(agent: DeterministicTacticalAgent) -> None:
    messages = [BattleStateMessage(state=state).to_dict() for state in _states(3)]
    # Un flux reel est mixte : les accuses ne sont pas des etats.
    messages.append(
        ActionResultMessage(
            battle_id="bridge-test",
            result=ActionResult(action_id="x", status=ActionStatus.ACCEPTED),
        ).to_dict()
    )
    bridge = MockBridge.from_messages(messages)
    assert bridge.remaining_states == len(messages) - 1
    state = bridge.receive_state()
    assert state is not None
    assert agent.decide(state).actions


def test_reprise_apres_message_incomplet(agent: DeterministicTacticalAgent) -> None:
    """Un message tronque doit etre signale, sans empecher la suite du flux."""
    states = _states(3)
    messages = [BattleStateMessage(state=state).to_dict() for state in states]
    corrupted = dict(messages[1])
    del corrupted["battle_id"]

    accepted = []
    for payload in [messages[0], corrupted, messages[2]]:
        try:
            decoded = decode_message(payload)
        except SchemaError:
            continue  # le consommateur journalise et poursuit
        accepted.append(decoded)

    assert len(accepted) == 2
    bridge = MockBridge.from_states([message.state for message in accepted])  # type: ignore[union-attr]
    state = bridge.receive_state()
    assert state is not None
    assert agent.decide(state) is not None


def test_pont_ferme_refuse_les_envois(agent: DeterministicTacticalAgent) -> None:
    bridge = MockBridge.from_states(_states(2))
    state = bridge.receive_state()
    assert state is not None
    turn = agent.decide(state)
    bridge.close()
    with pytest.raises(RuntimeError, match="ferme"):
        bridge.send_actions(turn.actions)


def test_arret_d_urgence_coupe_les_ordres(agent: DeterministicTacticalAgent) -> None:
    bridge = MockBridge.from_states(_states(6))
    agent.trigger_emergency_stop()
    while (state := bridge.receive_state()) is not None:
        turn = agent.decide(state)
        bridge.send_actions(turn.actions)
        assert turn.actions == ()
    assert bridge.sent_actions == []


def test_etat_final_n_est_pas_commande(agent: DeterministicTacticalAgent) -> None:
    states = _states(4)
    final = states[-1].with_units(states[-1].units, phase=BattlePhase.FINISHED)
    turn = agent.decide(final)
    assert turn.actions == ()
    assert turn.skipped_reason == "bataille terminee"

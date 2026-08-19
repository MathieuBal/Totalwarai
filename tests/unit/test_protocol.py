"""Protocole du pont : enveloppes, dispatch et compatibilite de version."""

from __future__ import annotations

import pytest

from totalwar_ai.bridge.command_models import (
    ProbeBattleState,
    ProbeUnitState,
    orders_take_effect,
)
from totalwar_ai.bridge.protocol import (
    PROTOCOL_VERSION,
    ActionResultMessage,
    AgentActionsMessage,
    BattleStateMessage,
    IncompatibleProtocolVersionError,
    MessageType,
    check_version,
    decode_message,
    encode_message,
    is_compatible,
    parse_version,
)
from totalwar_ai.domain.actions import ActionResult, ActionStatus, ActionType, AgentAction
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.serialization import SchemaError
from totalwar_ai.domain.unit_state import Side, UnitRole


def test_message_etat_respecte_le_format_documente(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    payload = encode_message(BattleStateMessage(state=state))
    assert payload["protocol_version"] == PROTOCOL_VERSION
    assert payload["message_type"] == MessageType.BATTLE_STATE.value
    assert payload["battle_id"] == state.battle_id
    assert "units" in payload["payload"]


def test_decodage_par_type(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    decoded = decode_message(BattleStateMessage(state=state).to_dict())
    assert isinstance(decoded, BattleStateMessage)
    assert decoded.state.units[0].id == "a1"

    actions = AgentActionsMessage(
        battle_id="b",
        sequence=3,
        actions=(AgentAction(type=ActionType.HOLD_POSITION, actor_ids=("a1",)),),
    )
    assert isinstance(decode_message(actions.to_dict()), AgentActionsMessage)

    result = ActionResultMessage(
        battle_id="b", result=ActionResult(action_id="x", status=ActionStatus.ACCEPTED)
    )
    assert isinstance(decode_message(result.to_dict()), ActionResultMessage)


def test_type_de_message_inconnu() -> None:
    with pytest.raises(SchemaError, match="Type de message inconnu"):
        decode_message({"protocol_version": PROTOCOL_VERSION, "message_type": "telemetrie"})


def test_compatibilite_de_version() -> None:
    assert is_compatible("0.1.4", "0.1.0")  # un correctif ne casse pas le format
    assert not is_compatible("0.2.0", "0.1.0")
    assert not is_compatible("1.1.0", "0.1.0")


def test_version_incompatible_levee(make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    state = make_battle([make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY)])
    payload = BattleStateMessage(state=state).to_dict()
    payload["protocol_version"] = "9.9.9"
    with pytest.raises(IncompatibleProtocolVersionError):
        decode_message(payload)


def test_version_malformee() -> None:
    with pytest.raises(SchemaError, match="malformee"):
        parse_version("0.1")
    with pytest.raises(SchemaError, match="malformee"):
        check_version("zero.un.zero")


def test_message_tronque_est_signale() -> None:
    with pytest.raises(SchemaError, match="obligatoire"):
        decode_message({"protocol_version": PROTOCOL_VERSION, "message_type": "battle_state"})


# --- la phase jouable, definie une seule fois --------------------------------


@pytest.mark.parametrize(
    ("phase", "jouable"),
    [
        ("", False),
        ("unknown", False),
        ("Startup", False),
        ("Deployment", False),
        ("Deployed", True),
        ("VictoryCountdown", False),
        ("Complete", False),
    ],
)
def test_les_deux_etats_repondent_la_meme_chose_sur_la_phase(phase: str, jouable: bool) -> None:
    """Une regle dupliquee finit par diverger — et celle-ci avait divergé.

    `ProbeUnitState` refusait `unknown` pendant que `ProbeBattleState` l'acceptait
    encore. C'est `ProbeBattleState` que `LiveSession` consulte : le durcissement
    n'avait jamais atteint le pilotage, et le test qui le validait interrogeait
    l'autre classe.

    La chaine vide etait toleree comme « champ absent d'un vieil enregistrement ».
    Mais le pont live ne relit pas d'enregistrements : la compatibilite des vieux
    corpus appartient a leur lecteur, pas au contrat du pont.
    """
    unite = ProbeUnitState(unit_id="1001", position=Vector3(0.0, 0.0, 0.0), phase=phase)

    assert orders_take_effect(phase) is jouable
    assert unite.orders_take_effect is jouable
    assert ProbeBattleState(phase=phase).orders_take_effect is jouable

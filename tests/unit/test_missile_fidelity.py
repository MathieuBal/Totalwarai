"""Le simulateur ne doit pas inventer de differences que le jeu ne fait pas."""

from __future__ import annotations

from totalwar_ai.domain.actions import ActionType, AgentAction
from totalwar_ai.domain.geometry import Vector3
from totalwar_ai.domain.unit_state import Side, UnitRole
from totalwar_ai.simulation.environment import SimulationEnvironment, UnitSpec


def _unite(unit_id: str, role: UnitRole, side: Side, z: float) -> UnitSpec:
    return UnitSpec(id=unit_id, side=side, role=role, position=Vector3(0.0, 0.0, z))


def test_une_unite_de_tir_qui_se_replie_continue_de_tirer() -> None:
    """`RETREAT` et `MOVE_GROUP` sont **la meme commande** vers le jeu.

    `bridge.orders.OrderTranslator.destination_keys` traduit les deux par un
    simple deplacement : aucune bataille reelle ne peut donc distinguer un
    tireur « en repli » d'un tireur « en mouvement ». Le simulateur les
    distinguait et faisait taire le premier, ce qui penalisait l'agent pour
    quelque chose d'impossible en jeu.
    """
    env = SimulationEnvironment(
        "t",
        (
            _unite("a_arc", UnitRole.RANGED_INFANTRY, Side.ALLY, 0.0),
            _unite("e_inf", UnitRole.MELEE_INFANTRY, Side.ENEMY, 60.0),
        ),
        seed=1,
        ally_autopilot=False,
    )
    depart = env.units["e_inf"].hp_ratio

    env.apply_actions(
        [
            AgentAction(
                type=ActionType.RETREAT,
                actor_ids=("a_arc",),
                parameters={"destination": Vector3(0.0, 0.0, -40.0)},
            )
        ]
    )
    for _ in range(20):
        env.step()

    assert env.units["a_arc"].ammo < env.units["a_arc"].max_ammo, "le tireur doit avoir tire"
    assert env.units["e_inf"].hp_ratio < depart, "l'ennemi doit avoir encaisse"

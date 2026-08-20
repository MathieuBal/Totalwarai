"""L'agent continue-t-il de donner des ordres quand rien n'aboutit ?

**Pourquoi ce fichier existe.** Deux batailles pilotees de suite ont vu l'armee
s'immobiliser : douze deplacements a t=3 s, puis trente ordres en sept cents
secondes, l'operateur devant jouer a la place de l'agent. Le planificateur
proposait pourtant le bon deplacement a chaque cycle — il etait ecarte comme
« deja actif », indefiniment.
"""

from __future__ import annotations

from totalwar_ai.agent.explainability import decide
from totalwar_ai.agent.tactical_agent import DeterministicTacticalAgent
from totalwar_ai.config import load_config
from totalwar_ai.domain.actions import ActionType, AgentAction, Formation
from totalwar_ai.domain.geometry import Vector3


def _ordre(unit_id: str = "a1") -> object:
    return decide(
        AgentAction(
            type=ActionType.MOVE_GROUP,
            actor_ids=(unit_id,),
            parameters={
                "destination": Vector3(0.0, 0.0, 300.0).to_dict(),
                "formation": Formation.LINE.value,
            },
            confidence=0.9,
        ),
        "rejoindre la ligne",
        "tenir le front",
        confidence=0.9,
    )


def _agent(tmp_path) -> DeterministicTacticalAgent:  # type: ignore[no-untyped-def]
    return DeterministicTacticalAgent.from_config(load_config(data_dir=tmp_path))


def test_un_ordre_identique_est_tu_tant_qu_il_est_en_cours(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """L'anti-repetition garde sa raison d'etre : ne pas saturer le jeu."""
    agent = _agent(tmp_path)

    gardes, tus = agent._drop_duplicates([_ordre()], 0.0)
    assert len(gardes) == 1 and tus == 0

    gardes, tus = agent._drop_duplicates([_ordre()], 1.0)
    assert gardes == [] and tus == 1


def test_un_ordre_identique_repart_une_fois_perime(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """**Le defaut qui a immobilise l'armee.**

    Le jeu rend la main sur chaque unite au bout de cinq secondes ; celle-ci
    s'arrete alors sans avoir atteint son point. Une signature retenue pour
    toujours transformait l'anti-repetition en paralysie.
    """
    agent = _agent(tmp_path)
    agent._drop_duplicates([_ordre()], 0.0)

    gardes, tus = agent._drop_duplicates([_ordre()], agent.order_ttl + 0.1)
    assert gardes, "l'unite immobile n'a jamais recu son ordre une seconde fois"
    assert tus == 0


def test_deux_unites_ne_partagent_pas_leur_memoire(tmp_path) -> None:  # type: ignore[no-untyped-def]
    agent = _agent(tmp_path)
    agent._drop_duplicates([_ordre("a1")], 0.0)

    gardes, tus = agent._drop_duplicates([_ordre("a2")], 0.5)
    assert len(gardes) == 1 and tus == 0


# --- le pilotage manuel du joueur --------------------------------------------


def _tenir(*unit_ids: str):  # type: ignore[no-untyped-def]
    from totalwar_ai.agent.explainability import decide
    from totalwar_ai.domain.actions import ActionType, AgentAction

    return decide(
        AgentAction(type=ActionType.HOLD_POSITION, actor_ids=tuple(unit_ids)),
        "posture defend",
        "tenir la ligne",
        confidence=0.8,
    )


def test_l_agent_n_arrete_que_ce_qu_il_a_lui_meme_mis_en_mouvement(  # type: ignore[no-untyped-def]
    agent, make_unit, make_battle
) -> None:
    """**Trente arrets, six unites, aucune commandee par l'agent.**

    Constate en bataille : a l'instant precis ou le joueur reprend son armee
    apres six minutes d'inaction, l'agent envoie six arrets, zero deplacement,
    zero attaque. L'unite 1012 est arretee alors qu'elle n'a jamais recu le
    moindre ordre de mouvement de toute la partie.
    """
    from totalwar_ai.domain.unit_state import Side, UnitRole

    joueur = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, metadata={"idle": False})
    etat = make_battle([joueur])

    # Jamais commandee : l'arret disparait plutot que de la reprendre au joueur.
    assert agent._respect_manual_control([_tenir("a1")], etat) == ()

    # Commandee par l'agent : l'arret reste, c'est le defaut d'origine.
    agent._commanded.add("a1")
    gardees = agent._respect_manual_control([_tenir("a1")], etat)
    assert len(gardees) == 1
    assert gardees[0].action.actor_ids == ("a1",)


def test_la_prise_de_l_agent_expire_quand_l_unite_s_arrete(agent, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Ce qui repart apres un arret ne vient plus de lui.

    Rien n'est invente pour le savoir : `idle` est publie dans l'etat et deja lu
    par le traducteur pour decider si un `HOLD` devient un arret.
    """
    from totalwar_ai.domain.unit_state import Side, UnitRole

    agent._commanded.add("a1")
    immobile = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, metadata={"idle": True})
    agent._respect_manual_control([], make_battle([immobile]))
    assert "a1" not in agent._commanded, "l'unite s'est arretee : la prise expire"

    repartie = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, metadata={"idle": False})
    assert agent._respect_manual_control([_tenir("a1")], make_battle([repartie])) == ()


def test_un_ordre_de_mouvement_rend_l_unite_a_l_agent(agent, make_unit, make_battle) -> None:  # type: ignore[no-untyped-def]
    """Tout ce qui n'est pas un arret remet l'unite sous sa charge."""
    from totalwar_ai.agent.explainability import decide
    from totalwar_ai.domain.actions import ActionType, AgentAction
    from totalwar_ai.domain.geometry import Vector3
    from totalwar_ai.domain.unit_state import Side, UnitRole

    unite = make_unit("a1", Side.ALLY, UnitRole.MELEE_INFANTRY, metadata={"idle": False})
    etat = make_battle([unite])
    deplacement = decide(
        AgentAction(
            type=ActionType.MOVE_GROUP,
            actor_ids=("a1",),
            parameters={"destination": Vector3(0.0, 0.0, 50.0)},
        ),
        "avancer",
        "prendre le terrain",
        confidence=0.8,
    )

    assert len(agent._respect_manual_control([deplacement], etat)) == 1
    assert "a1" in agent._commanded
    assert len(agent._respect_manual_control([_tenir("a1")], etat)) == 1

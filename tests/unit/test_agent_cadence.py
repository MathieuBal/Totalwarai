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

"""Le banc rend-il deux fois le meme chiffre ?

**Pourquoi ce test existe.** Le banc a longtemps rendu « victoire » ou « nul »
pour `balanced_clash` a graine identique, selon le processus. La cause etait
`PYTHONHASHSEED` : `SimUnit.engaged_with` est un ensemble de chaines, les degats
de melee etaient appliques dans son ordre d'iteration, et cet ordre decide qui
meurt en premier.

Consequence, tant que cela a dure : **tout ecart de quelques points mesure au
banc pouvait n'etre que ce bruit.** Un banc de trente-trois batailles bouge de
trois points des qu'une seule bascule. Des regles ont ete jugees sur de tels
ecarts.

Un test qui tourne dans un seul processus ne peut pas voir ce defaut — le
hachage y est fixe une fois pour toutes. Il faut donc relancer l'interpreteur
avec deux graines de hachage differentes et comparer.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]

#: Rejoue quelques batailles et imprime leurs issues, rien de plus.
PROGRAMME = """
from totalwar_ai.config import load_config
from totalwar_ai.simulation.runner import run_battle, run_supervised_battle
from totalwar_ai.simulation.scenarios import ScenarioCatalog

config = load_config()
catalogue = {scenario.name: scenario for scenario in ScenarioCatalog().all()}
issues = []
for nom in ("balanced_clash", "outnumbered", "ranged_defense"):
    scenario = catalogue[nom]
    for graine in (11, 23, 37):
        issues.append(run_supervised_battle(scenario, seed=graine, config=config).outcome.value)
        issues.append(
            run_battle(
                scenario, seed=graine, config=config, repository=None, generate_report=False
            ).summary.outcome.value
        )
print(",".join(issues))
"""


def _issues(hash_seed: str) -> str:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed, PYTHONPATH=str(RACINE))
    acheve = subprocess.run(
        [sys.executable, "-c", PROGRAMME],
        capture_output=True,
        text=True,
        env=env,
        cwd=RACINE,
        timeout=600,
    )
    assert acheve.returncode == 0, acheve.stderr
    return acheve.stdout.strip()


def test_le_banc_ne_depend_pas_de_la_graine_de_hachage() -> None:
    """Deux graines de hachage, les memes issues.

    Sans quoi le banc n'est pas un instrument de mesure : il rend un chiffre
    different a chaque lancement, et l'on prend son bruit pour un resultat.
    """
    premier = _issues("0")
    second = _issues("1")

    assert premier, "le sous-processus n'a rien imprime"
    assert premier == second, (
        "le banc depend de PYTHONHASHSEED : un ensemble est itere quelque part "
        "dans la simulation, et son ordre change l'issue des batailles.\n"
        f"  PYTHONHASHSEED=0 : {premier}\n"
        f"  PYTHONHASHSEED=1 : {second}"
    )

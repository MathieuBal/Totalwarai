"""Point d'entree `python -m totalwar_ai`.

Le paquet installe une commande `totalwar-ai`, mais elle depend du `Scripts/`
de l'environnement virtuel dans le `PATH` — ce qui echoue des que la console
n'a pas ete ouverte avec l'environnement actif. `python -m totalwar_ai` marche
dans tous les cas, tant que c'est le bon interpreteur qui est appele.
"""

from __future__ import annotations

from totalwar_ai.cli import main

raise SystemExit(main())

# 0001 — Dataclasses de la bibliothèque standard plutôt que Pydantic

- **Statut :** acceptée
- **Portée :** `totalwar_ai.domain`, `totalwar_ai.bridge`

## Contexte

Le domaine doit valider des données venant d'une source externe et peu fiable
(un mod Lua), et les sérialiser en JSON dans les deux sens. Pydantic v2 fait
cela très bien et gratuitement.

## Décision

Utiliser des `dataclass` gelées de la bibliothèque standard, avec une validation
explicite centralisée dans `domain/serialization.py`. La seule dépendance
d'exécution du projet est PyYAML.

## Raisons

- Le `README.md` demande explicitement d'« éviter les dépendances opaques » ;
  le cœur du projet doit rester lisible et débogable sans connaître un
  framework tiers.
- Les messages d'erreur sont écrits pour le contexte du projet
  (« Le champ 'health_ratio' doit être compris entre 0 et 1 »), ce qui compte
  quand la source des données est un mod Lua qu'on écrit soi-même.
- Le projet doit pouvoir tourner sans accès réseau pour installer quoi que ce
  soit — utile pour un utilisateur qui clone le dépôt et lance les tests.

## Conséquences

- Chaque type porte un `to_dict()` / `from_dict()` écrit à la main, et tous
  passent par les mêmes helpers : ne pas valider ailleurs.
- Pas de génération automatique de schéma JSON. Si le mod Lua a besoin d'un
  schéma formel, il faudra l'écrire — `docs/protocol.md` en tient lieu pour
  l'instant.
- Si le nombre de types venait à croître fortement, cette décision mérite d'être
  reconsidérée : elle est peu coûteuse à inverser tant que la validation reste
  centralisée.

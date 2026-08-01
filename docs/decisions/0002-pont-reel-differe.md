# 0002 — Différer le pont réel jusqu'au spike de faisabilité

- **Statut :** acceptée
- **Portée :** `totalwar_ai.bridge`

## Contexte

Le `README.md` prévoit un `file_bridge.py` échangeant des messages par fichiers
locaux en append-only. Il serait facile de l'écrire tout de suite.

## Décision

Ne pas l'écrire. Le paquet `bridge` ne contient que le protocole, l'interface
abstraite `Bridge` et `MockBridge`.

## Raisons

- Le choix du mécanisme de communication dépend entièrement de ce que les
  scripts Lua de bataille autorisent réellement, et personne ne le sait encore
  (Phase 0, `docs/feasibility.md`).
- Un `FileBridge` écrit à l'aveugle serait du code non testé contre son
  interlocuteur réel : il donnerait l'illusion que l'intégration est amorcée
  alors qu'elle ne l'est pas.
- L'interface `Bridge` est le vrai livrable de découplage : elle garantit que
  l'ajout d'un adaptateur ne touchera ni l'agent, ni le domaine.

## Conséquences

- L'intégration au jeu reste entièrement à faire, et le dépôt le dit
  explicitement plutôt que de le laisser croire.
- `scripts/run_agent.py` montre la boucle exacte qu'un adaptateur réel devra
  alimenter, ce qui limite le travail de conception au moment venu.
- Les modules `learning/trainer.py`, `learning/evaluator.py` et
  `learning/checkpoints.py` du `README.md` sont différés pour la même raison :
  ils ne peuvent être conçus sérieusement qu'avec un volume réel de batailles
  enregistrées.

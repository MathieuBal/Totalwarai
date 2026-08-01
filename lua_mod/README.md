# Sonde Lua — prototype d'intégration à WARHAMMER III

Ce dossier contient **une seule chose** : un script de bataille minimal dont le
but est de répondre à une question, pas de jouer.

> Peut-on, depuis une bataille solo de *Total War: WARHAMMER III*, observer une
> unité, recevoir un ordre écrit par un programme Python, l'exécuter, et rendre
> la main au joueur ?

Ce n'est pas l'agent. Aucune tactique, aucune gestion d'armée, une seule unité à
la fois. L'agent vit dans `src/totalwar_ai/` et n'a pas connaissance de ce
dossier.

## Origine du code

`script/battle/mod/totalwar_ai_probe.lua` est une **implémentation originale**.

Les API du jeu qu'il emploie ont été identifiées en étudiant la documentation de
modding et, à titre de documentation de recherche, le mod tiers *AI General 3*.
Les constats sont résumés dans
[`docs/research/ai-general-3-findings.md`](../docs/research/ai-general-3-findings.md).

**Aucun fichier de ce mod tiers n'est présent dans ce dépôt** : ni `.pack`, ni
scripts Lua, ni fichiers d'interface, ni code MCT. Aucune portion substantielle
n'en est reprise. Ce dépôt ne redistribue pas de code tiers.

## Installation pour un essai

> **Retour du premier essai (01/08/2026) : le script n'a pas été chargé.** Le
> journal du jeu ne listait que les mods de `script/_lib/mod/`. Voir
> [`../docs/feasibility.md`](../docs/feasibility.md) pour l'analyse complète.
> D'où la consigne des deux emplacements ci-dessous.

1. Empaqueter le script dans un `.pack` de type *mod* (RPFM), **aux deux
   emplacements suivants** :

   ```text
   totalwar_ai_probe.pack
   └── script
       ├── _lib
       │   └── mod
       │       └── totalwar_ai_probe.lua     <- emplacement prouvé chargé
       └── battle
           └── mod
               └── totalwar_ai_probe.lua     <- emplacement à confirmer
   ```

   Le script refuse de s'exécuter deux fois : le second exemplaire chargé
   s'annonce puis s'arrête. Aucun risque à mettre les deux.

   Pièges constatés : aucun dossier ne doit précéder `script` (ni `lua_mod`, ni
   `Totalwarai-main`) ; l'extension doit être `.lua` et non `.lua.txt` ; le jeu
   et son launcher doivent être fermés pendant la modification du pack.

2. Déposer le `.pack` dans le dossier `data/` de l'installation du jeu et
   l'activer dans le gestionnaire de mods.
3. Créer le dossier d'échange **à la racine du dossier d'installation du jeu** :

   ```text
   <installation>/totalwar_ai/
   ```

   Les chemins du script sont relatifs au répertoire de travail du jeu — c'est
   ainsi qu'un mod existant lit sa propre configuration.

4. Côté Python :

   ```bash
   export TOTALWAR_AI_BRIDGE_DIR="<installation de WARHAMMER III>"
   python -m totalwar_ai.cli probe --status
   ```

## Ce que le script fait, dans l'ordre

| Cadence | Action |
| --- | --- |
| 1 s | publie l'état de la première unité alliée contrôlable dans `totalwar_ai_state.jsonl` |
| 0,5 s | lit `totalwar_ai_command.json`, exécute une commande jamais vue, écrit un accusé dans `totalwar_ai_ack.jsonl` |
| à la demande | libère l'unité et rend la main au joueur |

La toute première ligne exécutée du fichier écrit dans le journal du jeu :

```text
[totalwar_ai] === fichier charge (sonde v0.1.0) ===
```

C'est la preuve de chargement. Son absence signifie que le jeu n'a pas trouvé le
fichier — pas que la sonde a échoué. Le message suivant indique le contexte
(bataille, ou menu/campagne où il n'y a rien à faire).

Chaque message est **aussi** écrit dans le journal du jeu via `out()`, préfixé
par `[totalwar_ai]`. C'est le canal de repli si l'écriture de fichier s'avère
indisponible en contexte bataille — le point que ce prototype doit précisément
trancher.

## Sécurité

- **Multijoueur** : le script refuse de démarrer. En cas de doute sur le type de
  partie, il refuse également — le doute profite à la prudence.
- **Arrêt d'urgence** : créer le fichier `totalwar_ai/totalwar_ai_stop` suffit à
  tout libérer et à couper la lecture des commandes. Ce canal fonctionne même si
  l'analyse des commandes échoue. `FileBridge.abort()` le fait pour vous.
- **Restitution garantie** : toute unité prise est relâchée au plus tard après
  `release_after_ms` (5 s par défaut), même si elle marche encore.
- **Fin de bataille** : tout est libéré au passage en phase `Complete`.
- **Pas de rejeu** : une commande dont le numéro de séquence a déjà été traité
  est ignorée. Le numéro est consommé *avant* exécution, pour qu'une commande en
  échec ne soit pas retentée en boucle.

## Limites connues

- L'analyseur JSON est volontairement partiel : il ne comprend que les messages
  exacts du protocole de la sonde. Toute autre forme est rejetée, pas devinée.
- Une seule unité est observée : la première alliée contrôlable rencontrée.
- Aucun ordre autre que le déplacement n'est implémenté.

# Protocole d'échange

Version actuelle : **0.1.0** (`totalwar_ai.bridge.protocol.PROTOCOL_VERSION`).

**Il y a deux protocoles, et ce n'est pas un doublon.**

| | Protocole du domaine | Protocole de la sonde |
| --- | --- | --- |
| Module | `bridge/protocol.py` | `bridge/command_models.py` |
| Transport | en mémoire (`MockBridge`), simulateur | trois fichiers partagés avec le jeu |
| Vocabulaire | 11 types d'action tactique, `UnitState` complet | déplacer, attaquer, immobiliser, déléguer, reprendre |
| Statut | conçu, jamais exécuté contre le jeu | **exécuté en bataille, neuf essais** |

Le premier est la cible : il décrit tout ce qu'un agent tactique voudrait dire.
Le second est ce que le jeu accepte réellement d'entendre, mesuré et non
supposé. La [première partie](#protocole-du-domaine) documente l'un, la
[seconde](#protocole-de-la-sonde--échange-par-fichiers) l'autre, et
`ProbeUnitObservation.to_unit_state()` est le raccord entre les deux.

## Protocole du domaine

Il relie la source d'états (simulateur ou `MockBridge`) à l'agent Python. Il est
volontairement pauvre : trois types de messages, aucune notion de session, aucun
état partagé implicite.

### Règle de compatibilité

Elle vaut pour les **deux** protocoles : `command_models.py` appelle le même
`check_version` que `protocol.py`, sur chaque message reçu du Lua.

Deux versions sont compatibles si leurs numéros **majeur et mineur** sont
identiques. Un correctif ne doit jamais changer le format.

| Émetteur | Récepteur | Résultat |
| --- | --- | --- |
| `0.1.0` | `0.1.4` | accepté |
| `0.2.0` | `0.1.0` | `IncompatibleProtocolVersionError` |
| `1.0.0` | `0.1.0` | `IncompatibleProtocolVersionError` |

Un message incompatible ou malformé lève une `SchemaError` : il n'est jamais
accepté silencieusement, et jamais interprété partiellement. Le consommateur
peut le journaliser et poursuivre avec le message suivant.

### Tolérance aux valeurs inconnues

Une exception à la stricte validation : une **valeur d'énumération inconnue**
dans un champ optionnel retombe sur une valeur par défaut plutôt que de faire
échouer tout le message. Un mod plus récent qui enverrait `role: "necro_dragon"`
produit une unité de rôle `unknown`, que le classifieur pourra ensuite traiter.
Les champs obligatoires (`id`, `side`, `battle_id`) restent stricts.

### Messages

#### `battle_state` — jeu → agent

```json
{
  "protocol_version": "0.1.0",
  "message_type": "battle_state",
  "battle_id": "018f...",
  "sequence": 42,
  "game_time": 125.5,
  "payload": {
    "phase": "engagement",
    "units": [],
    "objectives": [],
    "metadata": {}
  }
}
```

`phase` ∈ `deployment`, `approach`, `engagement`, `pursuit`, `finished`.

Chaque unité de `payload.units` suit le schéma `UnitState` :

```json
{
  "id": "player_unit_07",
  "side": "ally",
  "role": "ranged_infantry",
  "position": {"x": 125.4, "y": 0.0, "z": 82.1},
  "heading": 1.57,
  "health_ratio": 0.84,
  "entity_ratio": 0.77,
  "morale": 61.0,
  "fatigue": 0.32,
  "ammo_ratio": 0.48,
  "is_engaged": false,
  "is_routing": false,
  "is_hidden": false,
  "current_target_id": null,
  "tags": ["missile", "armour_piercing"],
  "unit_key": "emp_crossbowmen",
  "metadata": {"missile_range": 120.0}
}
```

Notes à l'attention du futur mod Lua :

- `x` et `z` décrivent le plan du terrain, `y` l'altitude ;
- `health_ratio`, `entity_ratio`, `fatigue` et `ammo_ratio` sont bornés à [0, 1]
  et rejetés hors de cet intervalle ;
- `role` peut être omis : `unit_key` et `tags` suffisent au classifieur
  (`config/unit_roles.yaml`) ;
- `is_hidden` fait respecter la règle « pas de triche cachée » : l'agent ignore
  ces unités ;
- `metadata.missile_range` est facultatif mais améliore nettement le ciblage.

#### `agent_actions` — agent → jeu

```json
{
  "protocol_version": "0.1.0",
  "message_type": "agent_actions",
  "battle_id": "018f...",
  "sequence": 42,
  "actions": [
    {
      "action_id": "018f...",
      "type": "MOVE_GROUP",
      "actor_ids": ["player_unit_01", "player_unit_02"],
      "parameters": {
        "destination": {"x": 100.0, "y": 0.0, "z": 75.0},
        "formation": "line",
        "heading": 1.57,
        "spacing": 45.0
      },
      "reason": "maintenir la ligne de front",
      "confidence": 0.91
    }
  ]
}
```

Types d'action et paramètres attendus :

| Type | Paramètres |
| --- | --- |
| `HOLD_POSITION` | `heading` (optionnel) |
| `MOVE_GROUP` | `destination`, `formation`, `heading`, `spacing` |
| `ATTACK_TARGET` | `target_id` |
| `FOCUS_FIRE` | `target_id` |
| `PROTECT` | `protected_ids` |
| `FLANK` | `target_id`, `side` (`left` / `right`) |
| `RETREAT` | `destination` (optionnel), `threat_id` |
| `DISENGAGE` | `destination` (optionnel) |
| `CHASE_ROUTING` | `target_id` |
| `FORM_RESERVE` | `rally_point` |
| `REORIENT_FRONT` | `heading` |

`reason` et `confidence` ne sont pas décoratifs : ils alimentent le rapport
post-bataille et le seuil `agent.confidence_threshold`.

#### `action_result` — jeu → agent

```json
{
  "protocol_version": "0.1.0",
  "message_type": "action_result",
  "battle_id": "018f...",
  "action_id": "018f...",
  "status": "accepted",
  "error": null
}
```

`status` ∈ `accepted`, `rejected`, `blocked`, `failed`. **Toute** action reçoit
un accusé : un ordre qui disparaît en silence est un bogue impossible à
diagnostiquer plus tard.

## Protocole de la sonde — échange par fichiers

C'est le protocole réellement parlé avec *WARHAMMER III*. Il est plus pauvre que
celui du domaine, et cette pauvreté est un résultat de mesure, pas un choix de
confort : le bac à sable Lua de bataille ne donne accès ni au terrain, ni au
moral, ni à la fatigue, ni à la largeur des unités. Le recensement complet — ce
que le jeu expose et ce qu'il refuse — est dans
[`feasibility.md`](feasibility.md).

### Le transport : trois fichiers et une sentinelle

Le Lua de bataille ne dispose que de `io.open` sur des chemins **relatifs au
répertoire de travail du jeu**. Il n'y a ni socket, ni tube, ni mémoire
partagée. Tout passe donc par `<dossier du jeu>/totalwar_ai/` :

| Fichier | Sens | Mode | Écrit par |
| --- | --- | --- | --- |
| `totalwar_ai_state.jsonl` | jeu → Python | ajout | Lua, toutes les 1000 ms |
| `totalwar_ai_command.json` | Python → jeu | **remplacé** | Python, à chaque ordre |
| `totalwar_ai_ack.jsonl` | jeu → Python | ajout | Lua, à chaque commande lue |
| `totalwar_ai_stop` | Python → jeu | sentinelle | Python, arrêt d'urgence |

Le répertoire est résolu par `bridge/paths.py` : variable d'environnement
`TOTALWAR_AI_BRIDGE_DIR`, puis chemin explicite, puis emplacements Steam usuels.
Si rien n'est trouvé, la fonction le dit plutôt que de deviner.

Quatre conséquences de ce transport, toutes apprises en bataille :

- **Le fichier de commande est remplacé, pas complété.** Publier les
  déplacements puis les attaques ferait perdre les premiers sans que rien ne le
  signale. D'où le message `orders`, qui porte les deux en un seul objet.
- **Python écrit par `os.replace`.** Le Lua relit le fichier toutes les 500 ms ;
  sans écriture atomique il lirait un JSON tronqué.
- **Python lit les flux en binaire.** Le Lua écrit `\r\n` sous Windows ; compter
  un octet de fin de ligne faisait dériver la position de lecture d'un octet par
  ligne, et après 157 états la lecture reprenait au milieu d'une ligne.
- **Le numéro de séquence est repris du disque au démarrage.** Un processus
  Python qui repartait de 1 voyait le Lua refuser à juste titre une séquence
  déjà exécutée, tout en lisant un vieil accusé qu'il prenait pour le sien.

### Révision du script Lua

Le protocole a une version (`0.1.0`), le script Lua a une **révision** : un
entier incrémenté à chaque changement de comportement du script.

- Lua : `TOTALWAR_AI_PROBE_REVISION` dans `totalwar_ai_probe.lua` ;
- Python : `EXPECTED_PROBE_REVISION` dans `bridge/paths.py` ;
- révision courante : **10**.

Les deux ne peuvent pas diverger en silence :
`tests/integration/test_lua_protocol.py` lit le script Lua et compare. Côté
opérateur, `totalwar-ai probe --log` annonce `Pack a jour (revision N)` — c'est
la seule façon de savoir que le `.pack` reconstruit correspond bien au Python
installé, parce que le jeu charge un `.pack` et non le fichier source.

### Messages de la sonde

Tous portent `protocol_version` et un champ `type`. `decode_command` dispatche
sur ce champ ; un type inconnu lève `SchemaError`.

#### `battle_state` — jeu → Python

Toutes les unités alliées, et tout ce qui ne l'est pas, à chaque publication.

```json
{
  "protocol_version": "0.1.0",
  "type": "battle_state",
  "sequence": 42,
  "game_time_ms": 125500,
  "phase": "Deployed",
  "allies": [
    {
      "id": "3145728",
      "type": "wh3_main_tze_inf_blue_horrors_0",
      "position": {"x": 125.4, "y": 12.0, "z": 82.1},
      "controllable": true,
      "commanding": false,
      "idle": true,
      "alive": true,
      "routing": false,
      "shattered": false,
      "in_melee": false,
      "hidden": false,
      "can_fly": false,
      "hitpoints": 1.0,
      "men_alive": 120,
      "bearing": 1.57,
      "ammo": 480,
      "missile_range": 90.0
    }
  ],
  "enemies": []
}
```

**Un champ optionnel absent signifie « le jeu ne l'expose pas », et c'est
différent de zéro.** `unary_morale` et toutes les formes de fatigue sont
absentes du bac à sable ; un moral à zéro se confondrait avec une unité qui
rompt. `to_unit_state()` pose donc `morale_available: False` et
`fatigue_available: False` dans les métadonnées : toute règle de l'agent qui
s'appuie sur ces valeurs doit d'abord vérifier ces drapeaux.

Deux dérivations méritent d'être connues, parce qu'elles ont chacune coûté un
essai en bataille :

- **`missile` est une étiquette mesurée, pas déduite du nom.**
  `wh3_main_tze_inf_blue_horrors_0` ne contient que `_inf_`, mais le jeu lui
  donne 90 de portée et 480 munitions. La mesure prime sur la clé d'unité.
- **`ammo` est un total, pas un ratio.** Il est rapporté au maximum observé par
  `bridge/roster.py`, qui fait de même pour `men_alive`. Sans cette mémoire,
  `can_shoot` — qui exige `ammo_ratio > 0` — aurait interdit le tir à toute
  l'armée.

`phase` est la phase annoncée par le moteur (`Startup`, `PrebattleWeather`,
`PrebattleCinematic`, `Deployment`, `Deployed`, `VictoryCountdown`, `Complete`).
Elle décide de `orders_take_effect` : **avant `Deployed`, le moteur accepte un
ordre et l'acquitte, mais l'unité ne bouge pas** — constaté en bataille,
immobile 33 s durant après un ordre accepté.

#### `orders` — Python → jeu

Une manœuvre complète en un message : déplacements, attaques, immobilisations.

```json
{
  "protocol_version": "0.1.0",
  "type": "orders",
  "sequence": 7,
  "release_after_ms": 5000,
  "moves": [{"unit_id": "3145728", "destination": {"x": 100.0, "y": 0.0, "z": 75.0}}],
  "attacks": [{"unit_id": "3145729", "target_id": "4194304", "melee": false}],
  "halts": ["3145730"]
}
```

`melee: false` laisse une unité de tir tirer sur sa cible ; le forcer au corps à
corps lui ferait perdre son avantage. `release_after_ms` borne la durée pendant
laquelle le Lua garde la main sur les unités concernées.

`move_unit` et `move_units` sont les formes réduites du même ordre, conservées
pour le diagnostic (`probe --move`).

#### `delegate` / `reclaim` — Python → jeu

```json
{"protocol_version": "0.1.0", "type": "delegate", "sequence": 8,
 "unit_ids": ["3145728", "3145729"]}
```

`delegate` confie les unités à `script_ai_planner`, l'IA de bataille de Creative
Assembly. **C'est bien plus engageant qu'un ordre de déplacement** : il n'y a
pas de restitution automatique au bout de cinq secondes, les unités restent à
l'IA jusqu'à reprise explicite. Toutes les voies d'arrêt de la sonde défont la
délégation, parce qu'elles passent toutes par `PROBE:release_all()`.

`reclaim` reprend les unités. **Liste vide : tout est repris** et le
planificateur dissous — c'est la voie des arrêts d'urgence, qui ne doit rien
laisser derrière elle. **Liste renseignée : reprise partielle**, le reste de
l'armée continue d'être joué par l'IA du jeu. C'est cette seconde forme qui rend
la supervision possible.

#### `abort` — Python → jeu

Le Lua libère tout et cesse de lire les commandes. Redondant avec la sentinelle
`totalwar_ai_stop`, et c'est voulu : deux chemins indépendants valent mieux
qu'un seul pour reprendre la main sur son propre jeu.

#### `action_result` — jeu → Python

```json
{"protocol_version": "0.1.0", "type": "action_result", "sequence": 7,
 "status": "accepted", "error": null, "detail": {"note": "3 ordre(s) emis"}}
```

`status` ∈ `accepted`, `rejected`, `completed`, `released`. Les trois derniers
comptent comme un succès (`ProbeAck.accepted`). Une commande dont le numéro de
séquence a déjà été traité est **rejetée** : le Lua ne rejoue jamais un ordre.

`detail` ne porte qu'une clé, `note`, en texte libre : c'est une aide au
diagnostic pour l'opérateur, jamais une donnée dont le code dépend.

### Ce que l'agent sait dire et que le jeu n'entend pas

`bridge/orders.py` traduit les actions du domaine en ordres de la sonde. La
correspondance n'est pas totale, et l'écart est compté et nommé plutôt que
approximé :

| Action du domaine | Traduction |
| --- | --- |
| `MOVE_GROUP`, `RETREAT`, `DISENGAGE`, `FORM_RESERVE` | déplacements, étalés en ligne perpendiculaire au cap |
| `ATTACK_TARGET`, `CHASE_ROUTING` | attaque, `melee: true` |
| `FOCUS_FIRE` | attaque, `melee: false` |
| `FLANK` | déplacement calculé à `FLANK_OFFSET` du flanc demandé |
| `PROTECT` | déplacement sur le point d'interception protégé ↔ menace |
| `HOLD_POSITION` | immobilisation, **des seules unités qui bougent** |
| `REORIENT_FRONT` | **non traduisible** — le jeu n'offre pas d'ordre d'orientation |

`HOLD_POSITION` a longtemps été traduit par rien du tout : l'armée continuait
d'avancer pendant que l'agent croyait tenir sa position. `REORIENT_FRONT` reste
la seule action sans équivalent, et la doctrine actuelle ne l'emploie pas (voir
[`decisions/0004`](decisions/0004-reorientation-du-front-mesuree-puis-ecartee.md)).

### Les trois arrêts

Ils sont indépendants à dessein — le joueur doit pouvoir reprendre la main sur
son propre jeu même si le Python est planté ou le Lua muet :

1. **`totalwar-ai probe --abort`** publie un message `abort` *et* pose la
   sentinelle ;
2. **créer le fichier `totalwar_ai_stop` à la main** suffit : le Lua le teste
   toutes les 500 ms, **avant** d'analyser la moindre commande, donc il
   fonctionne même si l'analyse échoue ;
3. **`release_after_ms`** rend les unités toutes seules au bout de cinq secondes,
   même si plus personne n'écoute — sauf les unités déléguées, qui exigent une
   reprise explicite.

Tous convergent vers `PROBE:release_all()`, point de passage unique : c'est ce
qui garantit qu'aucune voie d'arrêt n'oublie de défaire la délégation.

## Implémenter un nouvel adaptateur

**Si la source parle la langue du domaine** — un simulateur, un banc d'essai, un
autre jeu — hériter de `totalwar_ai.bridge.base.Bridge` et implémenter
`receive_state`, `send_actions` et `poll_results`. Voir `bridge/mock_bridge.py`
comme référence, et `scripts/run_agent.py` pour la boucle attendue.

**Si elle ne la parle pas**, comme *WARHAMMER III*, ne pas forcer l'interface :
`FileBridge` ne l'implémente pas et s'en porte mieux. Le patron est alors celui
de `bridge/live.py` — un transport, un module de messages, un traducteur
d'ordres, une boucle. La règle qui compte est de faire remonter honnêtement ce
que la source **ne** fournit pas, plutôt que de laisser les valeurs par défaut
du domaine passer pour des mesures.

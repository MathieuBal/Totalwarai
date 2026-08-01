# Protocole d'échange

Version actuelle : **0.1.0** (`totalwar_ai.bridge.protocol.PROTOCOL_VERSION`).

Le protocole relie la source d'états (mod Lua demain, simulateur ou `MockBridge`
aujourd'hui) à l'agent Python. Il est volontairement pauvre : trois types de
messages, aucune notion de session, aucun état partagé implicite.

## Règle de compatibilité

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

## Tolérance aux valeurs inconnues

Une exception à la stricte validation : une **valeur d'énumération inconnue**
dans un champ optionnel retombe sur une valeur par défaut plutôt que de faire
échouer tout le message. Un mod plus récent qui enverrait `role: "necro_dragon"`
produit une unité de rôle `unknown`, que le classifieur pourra ensuite traiter.
Les champs obligatoires (`id`, `side`, `battle_id`) restent stricts.

## Messages

### `battle_state` — jeu → agent

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

### `agent_actions` — agent → jeu

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

### `action_result` — jeu → agent

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

## Implémenter un nouvel adaptateur

Hériter de `totalwar_ai.bridge.base.Bridge` et implémenter `receive_state`,
`send_actions` et `poll_results`. Voir `bridge/mock_bridge.py` comme référence,
et `scripts/run_agent.py` pour la boucle attendue.

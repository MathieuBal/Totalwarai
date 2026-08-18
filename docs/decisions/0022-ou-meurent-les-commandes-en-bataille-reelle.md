# 0022 — Où meurent les commandes en bataille réelle

**Statut :** retenu — LIVE-001 répondu, deux tiers des commandes muettes meurent
dans le pont — 18/08/2026

## La réponse

Session du 18/08 22h20, causalement propre (expériences actives désactivées,
zéro ligne `MISSILE`). Sur **102 tours où une décision était due et où rien n'est
sorti** :

| étage | tours muets | où il vit |
| --- | ---: | --- |
| `duplicate_suppression` | 40 | agent |
| **`micro_move`** | **36** | **pont** |
| **`translation`** | **26** | **pont** |
| `planner` | 0 | — |
| `confidence` | 0 | — |
| `safety` | 0 | — |
| `throttle` | 0 | — |
| `INVARIANT_VIOLATION` | 0 | — |

**Soixante-deux des cent deux tours muets — 61 % — meurent dans le pont**, aux
deux étages que le banc ne traverse jamais puisqu'il appelle l'agent directement.

C'est ce que l'ADR 0021 annonçait comme structurellement invisible au banc, et
c'est maintenant mesuré plutôt que supposé.

## Ce que la mesure écarte

**`PYTHON_LOOP` est écarté.** Aucun écart d'horloge murale supérieur à dix
secondes sur toute la bataille : la boucle Python n'a jamais décroché. Le
scénario « Lua publie, Python ne consomme plus, puis rattrape » ne s'est pas
produit — et il ne pouvait être tranché qu'avec les deux horloges.

**La livraison est saine.** 262 ordres envoyés, **260 acquittés**, 2 refusés,
**zéro `ack_timeout`**. Le pont livre ce qu'il envoie ; ce qui manque n'est jamais
parti.

**Le planificateur n'est jamais l'étage.** Il propose toujours quelque chose. Ses
motifs d'abstention ne portent que sur l'assaut de secteur —
`ASSAULT_ALREADY_RUNNING` 57, `DEFEND_WAITING` 45 — et n'ont donc jamais vidé le
tuyau à eux seuls.

## Ce qui a changé depuis le 18/08 au matin

| | 13h24 | **22h20** |
| --- | --- | --- |
| plus longue fenêtre sans commande | 364,0 s | **110,5 s** |
| première attaque | 852,1 s | **188,6 s** |
| unités disparues avant la 1ʳᵉ attaque | 3 contre 0 | **0 contre 0** |

Et le mode d'échec a changé de nature : l'agent a émis **93 ordres d'attaque**, et
la bataille se termine tout de même 8 contre 10, l'adversaire **n'ayant perdu
aucune unité**. Ce n'est plus la paralysie, c'est la non-conversion.

## Ce que cela ne dit pas

Le taux de non-conversion n'a pas été mesuré ; « 93 attaques pour zéro perte
adverse » demande sa propre mesure avant toute hypothèse. Et une bataille n'est
pas un échantillon.

**Aucun correctif n'est appliqué dans cette ADR.** L'étage est nommé ; ce qui
suit doit se mesurer avant de se coder, comme le reste.

## Une trace qui manquait au corpus

Les capteurs de la révision 16 — vitesses, effectifs initiaux, munitions
initiales, cible en cours — arrivent jusqu'à l'agent mais n'étaient **archivés
nulle part** : le corpus de cette bataille n'en porte aucun. Une bataille rejouée
plus tard n'aurait rien vu. Corrigé : `_unit_entry` les écrit, avec la convention
habituelle — un champ absent veut dire que le jeu ne l'expose pas, jamais zéro.

Reste à vérifier sur une session en révision 16 que le pont les reçoit
effectivement : le journal du 22h20 ne les montre que dans les lignes de
recensement, et l'état complet n'y est pas journalisé.

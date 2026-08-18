# 0022 — Où meurent les commandes en bataille réelle

**Statut :** diagnostic **clos** ; comportement **non corrigé**, repris par
LIVE-002 — 18/08/2026

```
LIVE-001 / DIAGNOSTIC : CLOSED
LIVE-001 / BEHAVIOR   : superseded by LIVE-002
```

Le silence est désormais **expliqué**. Il n'est pas réparé : la bataille contient
toujours une fenêtre de 110,5 s sans commande.

## Le compte

Session du 18/08 22h20, causalement propre — expériences actives désactivées,
zéro ligne `MISSILE`. Sur **102 tours où une décision était due et où rien n'est
sorti** :

| étage | tours | où il vit |
| --- | ---: | --- |
| `duplicate_suppression` | 40 | agent |
| `micro_move` | 36 | pont |
| `translation` | 26 | pont |
| `planner`, `confidence`, `safety`, `throttle`, `INVARIANT_VIOLATION` | 0 | — |

## Ce que ce compte ne dit **pas**

> **« 61 % des commandes meurent dans le pont »** — cette formulation était
> fausse, et je l'avais publiée.

Un tour `translation`, relevé tel quel à t=86,6 s :

```
decisions  : tenir la position (1010), (1011), (1012)
untranslated : []
```

**Rien n'a échoué à se traduire.** Le traducteur conclut, correctement, qu'
immobiliser une unité déjà immobile ne demande aucun ordre moteur. Même chose
pour `micro_move`, qui écarte des repositionnements de quelques mètres déjà
pratiquement atteints.

La formulation juste est donc :

> **61 % des tours sans commande atteignent les deux filtres du pont avec
> uniquement des intentions déjà satisfaites, ou sans ordre moteur utile.**

Le pont **révèle** le symptôme ; la cause est en amont, dans ce que l'agent
décide de vouloir.

## La cause, lue dans le journal

À **t=10,1 s**, sept décisions et trois ordres partis :

```
tenir la position (1003, 1004, 1005)     <- la ligne de front
prendre a revers  (1010, 1011, 1012)     <- les trois unités rapides
constituer une reserve (1002)
                                          <- les tireurs : rien
```

Seules les trois unités mobiles partent réellement. À **t=77,6 s**, la sécurité
les arrête toutes les trois — leur rapport local était devenu suicidaire, et elle
a raison.

La chaîne est cohérente de bout en bout :

1. la doctrine `DEFEND` fait **attendre** la masse ;
2. les seules unités parties le sont **seules**, et sont arrêtées ;
3. le planificateur répète alors des `HOLD` et de la réserve ;
4. ces intentions sont **déjà satisfaites** ;
5. le pont n'a plus rien à envoyer.

Ce n'est pas un bug mystérieux. C'est une armée qui n'attaque pas ensemble.

## Ce que la mesure écarte

* **`PYTHON_LOOP`** — aucun écart d'horloge murale au-delà de dix secondes sur
  toute la bataille. La boucle n'a jamais décroché. C'est ce que le `script_log`
  seul ne pouvait pas trancher, et pourquoi il fallait deux horloges ;
* **la livraison** — 262 envoyés, 260 acquittés, 2 refusés, **zéro
  `ack_timeout`** : le pont livre ce qu'il envoie ;
* **le planificateur** — jamais l'étage. Il propose toujours quelque chose.

## Ce que les 93 attaques ont produit

> **« 93 attaques, zéro conversion »** — trop sévère, et faux.

| | début | fin |
| --- | ---: | ---: |
| unités ennemies | 10 | **10** |
| force ennemie | 10,000 | **8,256** |
| hommes ennemis | 490 | **438** |
| force alliée | 12,000 | **3,648** |

Aucun régiment adverse détruit, mais **cinquante-deux hommes tombés** et un
sixième de leur force retirée. Le diagnostic juste est donc :

> **des dégâts réels mais dispersés, et aucune unité achevée.**

Ce qui pointe vers la concentration, l'engagement séquentiel, les soutiens
absents et les cibles non finies — pas vers « les attaques ne fonctionnent pas ».

## Une trace qui manquait au corpus

Les capteurs de la révision 16 **existent bien dans le flux brut de la sonde** —
`fast_speed`, `slow_speed`, `initial_men`, `starting_ammo`, `strategic_value`,
`fatigue_state`, et de vrais `current_target_id` sur les ennemis en cours de
bataille. Ce qui manquait est ailleurs : ils n'étaient **pas archivés dans le
corpus du `BattleRecorder`**, donc absents de tout rejeu ultérieur.
`_unit_entry` les écrit désormais.

C'est une correction d'archivage, **pas de perception** : rien ne manquait à
l'agent en direct.

> Deux formulations à retirer de mes comptes rendus. J'ai écrit que ces capteurs
> « n'étaient archivés nulle part », ce qui laissait croire à une absence de
> perception ; et que le `totalwar_ai_state.jsonl` reçu datait de la révision 15,
> **provenance que je n'avais pas vérifiée**.

## Ce qui suit

**P0 — LIVE-002 : cohésion de la manœuvre.** Faire arriver une force cohérente
au même endroit au même moment.

La dispersion des dégâts ne fait **pas** l'objet d'un chantier séparé : elle peut
être une conséquence directe de l'engagement au compte-gouttes. On mesurera si
elle s'améliore d'elle-même une fois la cohésion obtenue.

L'ordre est donc :

```
phase unknown  ->  instrumentation LIVE-002  ->  correction LIVE-002
               ->  missile A/B/A  ->  Sector Value
```

**`phase unknown` passe devant**, et c'est un correctif de protocole, pas un
changement tactique : le diagnostic de LIVE-001 étant clos, plus rien ne justifie
de conserver les treize ordres pré-`Deployed`. Les garder polluerait les
premières secondes de la mesure de cohésion — ordres fictifs, signatures
d'anti-répétition occupées, destinations mémorisées.

Ni `duplicate_suppression` ni « les 93 attaques » ne font l'objet d'un chantier
séparé : les deux se remesurent après LIVE-002, parce que les deux peuvent en
être des conséquences.

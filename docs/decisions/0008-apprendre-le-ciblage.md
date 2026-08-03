# 0008 — Apprendre le ciblage de l'IA du moteur

**Statut :** livré, mesuré contre la doublure, jamais confronté à une vraie
bataille — 03/08/2026

## Ce que cela remplacerait

Notre ciblage repose sur `TARGET_PRIORITY`, une table écrite à la main :
artillerie 1,00, tireurs 0,85, héros 0,80, seigneur 0,70. Personne n'a jamais
vérifié que l'IA du jeu pense cela. Elle est plausible ; elle n'est pas mesurée.

L'objectif retenu — apprendre en regardant jouer le moteur — commence
naturellement par là : **qui attaque-t-il, et avec quoi ?** C'est la décision
tactique la plus fréquente, la plus observable, et celle dont l'imitation
transfère le mieux d'une carte à l'autre.

## Compter les cibles frappées ne donne rien

Une cavalerie qui frappe des lanciers neuf fois sur dix n'aime pas les lanciers :
elle affronte une armée qui n'a presque que cela. **Sans dénominateur, on
apprend la composition des armées rencontrées, jamais une préférence.**

D'où l'affinité retenue : le rapport entre les choix observés et ceux qu'aurait
faits un tirage au sort **parmi ce qui était disponible à cet instant**. 2,0
signifie « pris deux fois plus souvent que le hasard ne l'aurait pris » ; 0,3,
trois fois moins ; 1,0, indifférent. C'est le seul repère qui ait un sens.

Chaque décision inférée porte donc désormais les rôles adverses vivants au
moment où elle a été prise (`Observation.available`).

## Ce que l'étalonnage a corrigé

La méthode est celle de l'ADR 0007 : la doublure a une politique **connue
exactement** — sa cavalerie va aux tireurs. La table doit ressortir cela.

**Elle ressortait l'inverse.** Premier relevé : `shock_cavalry → spear_infantry`
à 6,32, `shock_cavalry → ranged_infantry` à **0,00**.

La cause n'était pas dans l'apprentissage mais dans ce qu'on lui donnait. Une
unité au contact **ne choisit pas** son adversaire : elle subit celui qui l'a
rattrapée. Toutes les mêlées de la cavalerie étaient des lanciers, qui sont
simplement ce qui l'arrêtait en chemin. Apprendre de `engage`, c'est apprendre
**qui a intercepté qui** en croyant apprendre qui était recherché.

Seuls `fonce` et `tire` sont retenus. Le relevé devient alors :

| | affinité | choix / attendus |
| --- | --- | --- |
| `shock_cavalry → ranged_infantry` | **2,00** | 96 / 48,0 |
| `shock_cavalry → melee_infantry` | **0,00** | 0 / 48,0 |

C'est exactement la politique de la doublure, et c'est le maximum atteignable
quand deux rôles sont également disponibles. **L'instrument a été corrigé avant
d'avoir servi, sans jouer une seule bataille.**

## Mesurer sur ce qu'on n'a pas appris

Une table décrit toujours parfaitement les batailles dont elle est tirée. La
seule question qui vaille est : prédit-elle celles qu'elle n'a pas vues ?

La coupe est **chronologique** — apprendre le début d'un corpus, prédire sa fin.
Mélanger ferait fuiter la même bataille des deux côtés, et la précision annoncée
serait celle d'un modèle qui a déjà vu la réponse. Un test le vérifie sur un
corpus dont la politique s'inverse à mi-parcours : la table apprise doit se
tromper partout.

Deux étalons, dont l'un seul importe vraiment :

- **le hasard parmi le disponible** — ne pas le battre, c'est n'avoir rien
  appris du tout ;
- **`TARGET_PRIORITY`** — ne pas le battre, c'est n'apprendre rien **que nous ne
  sachions déjà**. C'est lui qui dira s'il faut remplacer la table ou la garder.

Le hasard se calcule au lieu de se tirer : la probabilité de tomber sur le bon
rôle est exactement sa part parmi les unités disponibles. Aucune graine, aucun
bruit.

## Ce que la mesure donne, et ce qu'elle ne prouve pas

`totalwar-ai learn --calibrate`, onze scénarios menés par la doublure :

| | valeur |
| --- | --- |
| décisions ciblées | 2200 |
| ambiguïté sans continuité | 29,9 % |
| ambiguïté avec continuité | **22,6 %** |
| choix retenus pour l'apprentissage | 610 |
| prédiction — modèle appris | **82,0 %** |
| prédiction — hasard | 60,8 % |
| prédiction — `TARGET_PRIORITY` | 60,7 % |

**Ces chiffres ne disent rien de l'IA du jeu.** Ils disent que l'instrument
retrouve une politique dont on connaît la réponse, et qu'il le fait mieux que
les deux étalons. C'était le seul but. Les chiffres de l'ADR 0007 sont repris
ici parce qu'une seule commande les reproduit désormais — ils étaient jusque-là
issus d'un script jeté après usage, ce que la conduite adoptée en ADR 0006
interdit.

Deux limites, à retenir avant de lire une future table apprise en jeu :

**L'affinité mesure ce qui est effectivement choisi, pas un goût pur.** La
doublure tire sur le plus proche, et sa table ressort quand même
`ranged_infantry → lord` à 4,03 — parce qu'un seigneur se tient devant. Position
et préférence se confondent dans la donnée. C'est acceptable pour imiter, et
trompeur si on lit la table comme une doctrine.

**La prédiction porte sur le rôle, jamais sur l'unité.** Deux lanciers côte à
côte sont indiscernables pour nos données ; une précision annoncée à l'unité
serait inventée. Laquelle des deux relève de la géométrie, pas du ciblage.

## Relire une bataille enregistrée

`learning/replay.py` refait le chemin inverse de l'enregistrement et rend les
états tels que la boucle les avait sous les yeux. Sans lui, le corpus de trente
batailles resterait des fichiers illisibles par le reste du projet.

Deux précautions y tiennent toute la valeur :

- **les ratios se recalculent comme en direct** — le jeu ne donne ni l'effectif
  nominal ni la dotation de munitions, seulement des totaux ; le maximum observé
  fait office de dénominateur, ici comme en bataille. Le calculer autrement
  ferait mentir toute comparaison entre une bataille rejouée et la même vue en
  direct ;
- **les rôles sont déduits, jamais lus** — par le même classifieur qu'en direct.

## Décision

`totalwar-ai learn --targets` apprend le ciblage des batailles **exploitables**
uniquement. Une bataille trouée ferait entrer des changements de cible
imaginaires : l'unité a changé d'adversaire entre deux états parce qu'il en
manque un au milieu, pas parce qu'elle l'a voulu.

Rien de tout cela ne touche à l'agent. La table apprise ne remplacera
`TARGET_PRIORITY` que si elle bat la table écrite à la main **sur des batailles
réelles**, et le banc ne pourra pas en décider — il ne connaît que la doublure.

**Un instrument étalonné sur une réponse connue avant d'être braqué sur une
inconnue.** C'est la seule chose que cette livraison prétend fournir.

# 0018 — Composer l'assaut par le temps d'arrivée, pas par la distance

**Statut :** retenu — part conservée 64 % → 79 %, délai 31 s → 20 s, rapport au
contact 0,96 → 1,18, banc inchangé sans régression — 17/08/2026

## Le défaut

La concentration locale choisissait un secteur où nous valions 1,50 contre 1,
puis composait l'assaut en prenant les unités **les plus proches** :

```python
disponibles.sort(key=lambda unit: (sector.centre.distance_2d(unit.position), unit.id))
```

L'instrument de conversion (ADR 0017) a mesuré ce que cela donnait sur
`outnumbered`, douze graines : **1,50 au choix, 0,96 au contact**, après 31 s de
trajet. L'assaut arrivait sous la parité, ayant perdu 36 % de son avantage en
chemin.

La cause tient à un manque, vérifié : **l'agent n'avait aucune notion de
vitesse** — ni `UnitState` ni aucun module d'`agent/` ne mentionnait `speed`. Or
le simulateur va de **1,6 m/s** pour l'artillerie à **8,5** pour la cavalerie de
choc. À deux cents mètres du secteur, ces deux-là comptaient pour la même chose
dans le numérateur du rapport local : l'une arrive en vingt-quatre secondes,
l'autre en deux minutes, et le rapport annoncé supposait les deux présentes.

## La vitesse s'observe, elle ne se lit pas

Le simulateur connaît `template.speed`. **Le jeu ne le donne pas** : le
recensement a essayé `speed`, absent. Bâtir l'ETA dessus rendrait le banc plus
juste et l'agent inapplicable en bataille — un canal privilégié de plus, du même
genre que celui que l'ADR 0015 a coûté à débusquer.

`agent/mobility.py` déduit donc la vitesse du **déplacement observé entre deux
états**, canal que le simulateur et le pont fournissent tous deux à 2 Hz.
Mesures réelles sur `outnumbered` : 4,77 et 4,89 m/s pour l'infanterie de lances,
2,97 pour les tireurs, 3,50 pour le seigneur — cohérent avec la configuration
sans jamais l'avoir lue.

Trois précautions, chacune tirée d'un défaut passé :

* **le seuil d'immobilité** vaut `STILL_DISTANCE = 3.0`, la même valeur que
  `learning.observation` — décider sur un seuil et mesurer sur un autre ne
  validerait rien ;
* **on retient le plus rapide observé**, lissé : une unité montre sa vraie
  vitesse quand elle marche librement, jamais quand elle contourne un obstacle,
  et une moyenne sous-estimerait systématiquement ce dont elle est capable ;
* **un plancher**, sans lequel une unité jamais vue marcher aurait un ETA infini
  et serait exclue de tout assaut à jamais.

## Ce que le premier réglage a produit — et qu'il fallait dire

`ASSAULT_DEADLINE` a d'abord été réglé à **45 s**, justifié par le délai médian
mesuré de 31 s. Résultat : **zéro assaut sur douze graines.**

Au premier plan, aucune unité n'a encore été vue marcher ; chacune porte la
vitesse par défaut, et 45 s à 4 m/s ne font que 180 m quand la ligne adverse est
à plus de deux cents.

> Le plan annonçait ce risque avant l'expérience — « composer par ETA va rendre
> certains assauts impossibles ; si le compte tombe à zéro, le réglage est trop
> strict et je le dirai ». Il est tombé à zéro.

La leçon dépasse le réglage : **le délai absolu n'est pas le bon instrument.**
Deux unités à trois cents mètres qui arrivent *ensemble* portent un coup
concentré. Ce qui tue un assaut est la **dispersion**, et c'est `ASSAULT_WINDOW`
qui s'en charge. Le délai ne sert donc plus qu'à écarter ce qui ne peut pas
arriver du tout — 90 s, une borne qui ne doit pas mordre.

## Ce que la mesure dit

`outnumbered`, douze graines, par l'instrument de l'ADR 0017 :

| | avant | après |
| --- | --- | --- |
| rapport au choix | 1,50 | 1,50 |
| **rapport au contact** | **0,96** | **1,18** |
| part conservée | 64 % | **79 %** |
| délai médian | 31 s | **20 s** |
| assauts jamais arrivés | 0 | 0 |

**L'assaut arrive désormais au-dessus de la parité.** Le banc reste à 82 % de
victoires, forces restantes 80 %, aucune régression.

## Ce que cela ne fait pas

`outnumbered` reste un nul et **Gate A n'est pas franchie** — le verdict le dit à
chaque exécution du banc. Un rapport de 1,18 au contact est un progrès net sur
0,96, pas encore une victoire.

Ce qui suit se mesure avant de se coder, comme ici : le rapport tient maintenant
jusqu'au contact, donc la question « pourquoi le secteur ne rompt-il pas ? » se
pose enfin sur les bons termes — ciblage, concentration du feu, achèvement — et
plus sur la vitesse.

Le simulateur est plat et sans moral, et son ETA est une distance à vol d'oiseau :
le jeu expose `can_reach_position`, jamais une longueur de chemin. Ce que l'on
calcule est un **plancher**, suffisant pour comparer deux unités entre elles, ce
qui est le seul usage qu'on en fait.

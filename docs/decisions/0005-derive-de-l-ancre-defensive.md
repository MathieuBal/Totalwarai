# 0005 — La dérive de l'ancre défensive n'est pas corrigée

**Statut :** constaté, mesuré, non corrigé — 02/08/2026

## Le fait

En posture de maintien (`defend`, `delay`), le dispositif se place par rapport à
une ancre calculée depuis nos propres unités, avec des décalages vers l'arrière :
tireurs en retrait, artillerie plus loin, réserve plus loin encore. Chaque
recalcul repousse donc l'ancre d'un cran, et l'armée recule sans fin.

Mesuré sur `skirmish_standoff`, où l'adversaire n'avance jamais :

```
t=  0s  ancre_z=  -70   nous_z=  -78   eux_z= +177
t=150s  ancre_z= -202   nous_z= -160   eux_z= +177
t=376s  ancre_z= -264   nous_z= -198   eux_z= +177
```

L'ennemi n'a pas bougé d'un mètre. L'armée a reculé de deux cents mètres sans
tirer un coup. Sur quatre cents secondes, l'agent a produit quatorze
`MOVE_GROUP`, quarante `FORM_RESERVE`, quarante-trois `HOLD_POSITION` — et
**zéro attaque**.

Le commentaire de `_line_anchor` montre que cette boucle avait déjà été
identifiée, et qu'ancrer sur la ligne de front plutôt que sur toute l'armée
devait la fermer. C'est insuffisant : la ligne de front elle-même finit par être
repoussée.

## Ce qui a été tenté

**Première tentative — rompre l'impasse.** Passer en `advance` après quatre-vingt
-dix secondes sans contact. Mesure sur `skirmish_standoff` :

| Comportement | Issue | Nos forces |
| --- | --- | --- |
| attendre (actuel) | nul | **100 %** |
| avancer | **défaite** | 59 %, ennemi intact |

L'avance n'atteint jamais le contact : l'armée encaisse des tirs en marchant,
puis se fige à quatre-vingt-dix-huit mètres. Retirée.

**Seconde tentative — figer l'ancre.** Conserver l'ancre d'une posture de
maintien d'un recalcul à l'autre, et ne la recalculer qu'au changement de
posture ou lorsque l'ennemi s'est notablement déplacé. La dérive disparaît :
l'ancre reste à −70, l'armée se stabilise à −103 au lieu de −198.

Mais le banc mesure une régression franche :

```
balanced_clash — taux de victoire : 100% -> 0%
outnumbered    — forces restantes :  46% -> 30%
```

Une première version comparait la position adverse à celle du **dernier**
recalcul plutôt qu'à celle du gel — l'ennemi avançait de vingt mètres par cycle
sans jamais franchir le seuil. Corrigé, le résultat reste régressif. Retirée
également.

**Troisième tentative — imposer une posture offensive.** L'opérateur ayant
observé en jeu une armée qui « bouge puis plus rien », l'hypothèse évidente était
qu'une posture `advance` ou `envelop` la ferait engager. Mesuré contre le faux
jeu, ennemi à soixante mètres, sur vingt tours :

| Posture imposée | Ordres émis |
| --- | --- |
| aucune (l'agent choisit) | 4 dép. / 2 att. / 1 arrêt, puis silence |
| `advance` | **identique** |
| `envelop` | **identique** |
| `defend` | 2 dép., puis silence |
| `delay` | 2 dép., puis silence |

**La posture ne change rien à l'engagement.** Les deux postures offensives
produisent exactement la même séquence que le choix libre de l'agent. Ce n'est
donc pas le levier, et il ne faut pas le recommander comme tel.

## Ce que l'agent ne fait pas — et ce qu'il fait bien

Le même banc lève un doute qui traînait : **l'agent n'est pas figé**. Sur un
monde immobile il se tait après trois salves, mais dès que l'adversaire avance
de six mètres par tour, il redécide sans discontinuer :

```
tour 10  1 dep      tour 12  1 att      tour 14  1 dep
tour 16  1 att      tour 18  1 arret    tour 20  4 dep
```

Le silence observé n'est donc pas une panne de la boucle de décision : c'est un
plan **satisfait**. L'armée prend sa formation, l'adversaire n'avance pas, et
plus rien ne justifie un ordre nouveau. C'est exactement l'impasse décrite plus
haut, vue depuis l'autre bout.

## Pourquoi cela reste ouvert

La dérive est **porteuse** dans le simulateur : céder du terrain sous la
pression y gagne des batailles. La supprimer coûte `balanced_clash` en entier.

Le même comportement est **manifestement faux dans le jeu** : l'opérateur y voit
une armée qui recule indéfiniment sans combattre.

Les deux observations sont valides, et je ne sais pas laquelle arbitre. Le
simulateur a été écrit ici : son modèle de combat récompense peut-être le recul
bien au-delà de ce que fait WARHAMMER III. Trancher demande de mesurer des
batailles réelles, pas d'ajuster à nouveau contre un juge dont la fidélité est
elle-même en question.

## Décision

Ne rien corriger tant que la mesure ne peut pas départager. Sont conservés :

* le scénario `skirmish_standoff`, qui reproduit le cas et le gardera visible ;
* ce document, pour que la prochaine tentative parte de ce qui a déjà été
  mesuré plutôt que de le redécouvrir.

**Le préalable à toute correction** est d'enregistrer des batailles réelles
pilotées et de les comparer aux batailles simulées. Sans cela, chaque
ajustement se fera contre un simulateur dont on ignore s'il dit vrai.

## Le préalable est levé — première mesure réelle

Essai n° 11, escarmouche, armée peaux-vertes de quinze unités dont treize
confiées :

| Mode | Issue | Nos forces | Durée |
| --- | --- | --- | --- |
| `--observe` (IA du jeu seule) | **victoire** | 87 % | 237,8 s |
| `--supervise` | inconnue — session interrompue | 93 % | 89 s |
| `--play` (notre agent) | non enregistrée | — | — |

C'est le premier chiffre auquel comparer quoi que ce soit : **l'IA du moteur
gagne en gardant 87 % de l'armée**. Toute correction de la dérive devra faire
au moins aussi bien, mesurée en jeu et non plus seulement au banc.

Deux réserves à ne pas perdre de vue. La supervision n'a produit **aucune
intervention** : l'armée était en mêlée pure — ni artillerie, ni tir, et un
seigneur jamais descendu sous le seuil critique — donc aucune des trois règles
ne pouvait se déclencher. Cette ligne ne dit rien de la supervision. Et
l'opérateur juge l'IA du moteur « pas forte » en attaque : la référence est un
plancher mesuré, pas un plafond.

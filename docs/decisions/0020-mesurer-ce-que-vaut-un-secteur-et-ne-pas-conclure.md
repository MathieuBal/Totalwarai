# 0020 — Mesurer ce que vaut un secteur, et refuser de conclure

**Statut :** retenu — la mesure existe, elle ne tranche pas, et c'est le
résultat ; banc inchangé à 82 % / 80 %, aucune régression — 18/08/2026

## La question posée

L'ADR 0019 a localisé le défaut : sur `skirmish_standoff`, le seul secteur qui
atteigne jamais le rapport requis est un régiment **isolé**, à 4,92 contre 1,17
et 1,23 ailleurs. L'enfoncer transforme le nul en défaite — 74 % de nos forces
restantes contre 98 % des leurs.

L'agent sait répondre à « où suis-je le plus fort ? ». Il ne sait pas demander
« et ensuite ? ». La question de ce chantier était donc, **avant tout score** :

> Existe-t-il un attribut qui aurait écarté ce secteur-là sans écarter les
> assauts que l'agent gagne déjà ?

## L'instrument

`best()` ne choisit qu'un secteur par état, si bien qu'aucune donnée n'existe sur
ce que les autres auraient donné. `totalwar-ai sectors` rejoue donc chaque
bataille en **imposant** chaque secteur tour à tour (`Planner.forced_sector`),
fenêtre d'arrivée glissante activée — sans quoi `commit()` refuse tout.

Deux honnêtetés, publiées avec les chiffres :

* la mesure répond à « ce que ce secteur donnerait **si l'on pouvait y composer
  un assaut** », et non à « ce que l'agent ferait » ;
* forcer un secteur n'est pas jouer : ces batailles ne comptent dans aucun taux
  de victoire, le banc ne bouge pas, et un test vérifie que les deux canaux —
  secteur imposé et fenêtre glissante — sont éteints par défaut.

Neuf attributs sont relevés par secteur candidat, dont `expected_followup` : à la
rupture, le meilleur rapport local encore atteignable. C'est le chiffre qui
n'existait nulle part, et il répond littéralement à « si je gagne ici, qu'est-ce
que cela me permet de faire juste après ? ».

## Ce que le premier passage a failli publier

11 scénarios × 12 graines × 3 secteurs, **96 relevés**. La lecture donnait ceci :

| secteur valant moins d'un quart de leur armée | échange médian | profitables | ruptures |
| --- | --- | --- | --- |
| **oui** (12) | **−1,81** | **0/12** | **0/12** |
| non (84) | −0,09 | 36/84 | **84/84** |

Une séparation parfaite. Tout invitait à en faire le premier terme d'un score.

**C'était un artefact.** Les 96 relevés ne portent que **quatre formes de
secteur**, une par scénario, répétée sur douze graines et trois indices :

```
rout_pursuit           1 ennemi, 100 % de leur armée
numerical_superiority  1 ennemi,  33 %
outnumbered            2 ennemis, 33 %
skirmish_standoff      1 ennemi,  12 %   <- le seul mauvais
```

« Secteur valant moins d'un quart de leur armée » ne sélectionne rien d'autre que
`skirmish_standoff`. L'attribut et le nom de la bataille désignent exactement les
mêmes combats, et **rien ne permet de dire lequel des deux explique l'issue**.

> Rejouer douze fois la même configuration ne fait pas douze mesures. Le seuil
> portait sur le nombre de relevés ; il porte désormais sur le nombre de
> **configurations distinctes**, et une lecture qui trouve un attribut sans
> aucun scénario des deux côtés le déclare confondu au lieu de le publier.

Quatre des cinq attributs examinés sont dans ce cas.

## Ce que la mesure dit, une fois le garde-fou en place

```
97 relevé(s) pour 5 configuration(s) distincte(s).
**Moins de 8 configurations : on ne conclut pas.**
```

**Le banc ne permet pas de répondre à la question.** Sur onze scénarios, quatre
seulement produisent un assaut, même secteur imposé et fenêtre glissante
activée ; les sept autres n'en composent aucun. Les batailles y sont courtes —
48 à 92 secondes, cinq à dix plans — et `commit()` n'y réunit jamais une fois et
demie le coût du secteur dans une seule fenêtre d'arrivée.

Conséquence directe, et elle mérite d'être dite : **aucun assaut observé ne vise
jamais d'artillerie, de seigneur ni de héros.** Les secteurs que l'agent sait
attaquer sont exactement ceux qui ne contiennent rien de précieux.

## Ce que j'en conclus, et ce que je refuse de conclure

C'est la branche « non » annoncée par le plan, et l'engagement était de la
publier telle quelle :

> Si la mesure ne désigne rien, l'ADR 0020 le dira au lieu de chercher le
> coefficient qui la sauve.

Je ne propose donc **aucun score de secteur**. La formule esquissée — avantage
local × valeur des cibles × probabilité de rupture, moins le coût d'engagement —
reste la bonne logique ; l'écrire aujourd'hui reviendrait à en calibrer les
termes sur cinq points dont quatre sont des noms de scénarios.

**Le prochain obstacle n'est pas le score, c'est le banc.** Il faut des batailles
qui offrent des formes de secteur variées — détachements isolés *et* groupes
denses, cibles de valeur *et* piétaille, dans la même bataille — sans quoi aucun
attribut ne pourra jamais être distingué d'un nom de scénario. C'est un chantier
de scénarios, pas de doctrine, et il précède Sector Value v2.

## Ce que cela laisse

Banc inchangé : 82 % sur trois graines, 79 % sur douze, forces restantes 80 %,
aucune régression, aucune graine réservée touchée. Gate A n'est pas franchie et
le verdict le dit à chaque exécution.

Acquis quand même, et réutilisables tels quels : l'instrument, son garde-fou
contre le confondu, `expected_followup`, et une règle d'élargissement des graines
désormais **partagée** entre le banc et la sonde — deux règles de même intention
finissent toujours par diverger.

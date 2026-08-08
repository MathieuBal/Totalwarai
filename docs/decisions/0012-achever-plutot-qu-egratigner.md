# 0012 — Achever plutôt qu'égratigner

**Statut :** trois leviers corrigés, mesurés sur huit graines — 08/08/2026

## L'objection

> « Même au-delà du nombre, un bon tacticien sait diviser les forces. Même s'il
> perd, il devrait pouvoir faire tomber au moins un régiment ennemi. »

Elle est juste, et l'ADR 0010 n'y répondait pas. Le terme de concentration
introduit là-bas se contente de la **parité locale** : il évite les mauvais
combats, il n'en gagne aucun. Éviter de perdre n'est pas gagner.

## Ce que les batailles jouées disent

| | bataille 1 | bataille 2 |
| --- | --- | --- |
| dégâts infligés | **9,77 unités-équivalent** | 5,30 |
| unités adverses entamées | 19/20 | 14/14 |
| dégâts moyens par unité entamée | 51 % de sa barre | 38 % |
| unités adverses mises en déroute | 9 | 1 |
| **unités adverses détruites** | **0** | **0** |

Bataille 1 : de quoi abattre dix régiments, aucun abattu. Les vingt sont
repartis entre 18 % et 99 % de vie.

**Des dégâts étalés ne rendent rien.** Un régiment à 50 % se bat encore. S'il
rompt, il se rallie et il revient — ce qui explique que neuf déroutes adverses
n'aient pas suffi. Un régiment abattu ne revient jamais et ne rend plus un seul
coup : détruire une unité supprime ses dégâts futurs, l'entamer ne fait rien de
tel.

## Les trois causes, toutes dans `score_target`

**La prime aux cibles entamées ne pouvait rien décider.** Elle valait au plus
0,30 quand la proximité pesait jusqu'à 2,00. La distance décidait seule.

**La pénalité de saturation dispersait sans palier.** `score -= 0,20 * already`
— chaque allié déjà envoyé rendait la cible moins attirante, indéfiniment. C'est
une pression d'étalement permanente, et c'est littéralement l'instruction
« va égratigner quelqu'un d'autre ».

**La pénalité sur les fuyards était à l'envers.** `score -= 0,60` sur une unité
en déroute : nous évitions précisément les unités qu'un dernier choc détruit
pour de bon. La pénalité subsiste — poursuivre à travers la carte reste le piège
classique — mais réduite à 0,20, la distance se chargeant déjà de décourager la
poursuite lointaine.

## Ce qui a été mesuré

Huit graines, onze scénarios, 408 unités adverses, sur l'instrument corrigé de
l'ADR 0011 :

| | victoires | forces | unités adverses détruites | rendement |
| --- | --- | --- | --- | --- |
| avant | 82 % | 78 % | 82/408 | 0,29 |
| après | 82 % | 77 % | **113/408** | **0,40** |

Le rendement est le nombre d'unités abattues par unité-équivalent de dégâts
infligée. C'est lui qui distingue un combat concentré d'un combat étalé : la même
quantité de dégâts peut abattre cinq régiments ou n'en abattre aucun.

**+38 % d'unités adverses détruites, à taux de victoire et survie du seigneur
inchangés, pour un point de forces restantes.** Le point perdu est le prix de
l'insistance — on presse au lieu de se redéployer.

### Deux réglages tranchés par la mesure, pas par l'intuition

**Le tir se concentre aussi.** La règle en place dispersait les tireurs au
troisième. Sur huit graines : 113 unités détruites en concentrant le tir contre
105 en le dispersant, à taux de victoire égal.

**La prime se calcule sur `effective_strength`, pas sur le seul compte
d'hommes.** C'était contre-intuitif : le compte d'hommes semble la mesure directe
de la destruction. Mais `effective_strength` pénalise la déroute (×0,1) et la
fatigue, donc elle désigne aussi l'unité qui vient de rompre — celle qu'un
dernier choc achève. Le compte d'hommes seul donnait 84 unités détruites contre
113, et coûtait cinq points de victoires quand on y ajoutait le tir concentré.

## Ce que cela ne prouve pas

Le banc n'a pas de moral. Il ne peut donc pas montrer le gain principal attendu
en jeu : **empêcher qu'un régiment mis en déroute se rallie et revienne**. Neuf
déroutes adverses pour zéro destruction, c'est exactement ce que le banc ne sait
pas reproduire.

Ce qu'il montre, et qui suffit à expédier : à taux de victoire égal, l'agent
détruit 38 % d'unités adverses en plus avec les mêmes dégâts.

> La mesure qui tranchera est `learn --units`, section « ce que nos dégâts ont
> acheté », sur la prochaine bataille jouée. Le rendement doit quitter **0,00**.
> Un seul régiment adverse détruit vaudrait plus que tout ce tableau.

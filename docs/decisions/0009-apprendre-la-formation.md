# 0009 — Apprendre où l'IA du moteur place ses unités

**Statut :** livré, mesure étalonnée sur des états construits, jamais confrontée
à une vraie bataille — 03/08/2026

## Ce qui manquait

L'ADR 0008 apprend *qui* attaquer. Cela ne dit pas *où se tenir* — et c'est là
que notre agent est le plus faible. Il ne voit ni le terrain ni les formations,
et place ses unités d'après des constantes écrites à la main. L'IA du moteur
place les siennes en connaissance de cause, et cela s'observe sans rien lui
demander.

## Tout est relatif, sinon rien ne transfère

Une position absolue ne veut rien dire d'une carte à l'autre. Trois grandeurs
seulement sont retenues, toutes relatives à l'armée elle-même :

| Grandeur | Ce qu'elle dit |
| --- | --- |
| **profondeur** | le long de l'axe vers l'ennemi. Les tireurs sont-ils derrière, et de combien ? |
| **écart au centre** | latéralement. La cavalerie tient-elle les ailes ? |
| **espacement** | distance à l'allié le plus proche. Ligne serrée ou étalée ? |

L'axe est celui qui va du centre de notre armée à celui de l'adversaire. Une
même formation tournée de quatre-vingt-dix degrés donne donc exactement les
mêmes chiffres — un test le vérifie, parce que c'est précisément ce qui rend la
mesure transportable.

## Une formation ne se mesure qu'avant le choc

Dès que la mêlée commence, les lignes s'interpénètrent et la formation cesse
d'exister. Mesurer la place des tireurs pendant une mêlée générale reviendrait à
mesurer du désordre.

Seuls les états **d'approche** comptent : armées séparées de plus de soixante
mètres, aucune unité au contact. Une seule unité engagée suffit à défaire la
formation autour d'elle et disqualifie l'état.

C'est le même constat qu'en ADR 0008, où la mêlée n'est pas un choix de cible.
**Une bataille se lit dans les instants qui précèdent le choc** — ce qui vient
après en dit beaucoup moins qu'il n'y paraît.

## Ce que l'étalonnage peut, et ce qu'il ne peut pas

La doublure n'a **aucune** logique de formation : elle marche droit sur sa
cible. Elle ne peut donc pas servir de réponse connue, contrairement au ciblage.
Le dire est plus utile que de fabriquer une validation qui n'en serait pas.

L'étalonnage porte sur **l'instrument seul**, contre des états construits à la
main dont la géométrie est connue au mètre près : des tireurs posés vingt mètres
en arrière doivent ressortir à vingt, une cavalerie posée à cent cinquante
mètres du centre doit ressortir à cent cinquante, une formation tournée doit
donner les mêmes chiffres.

Ce que `learn --calibrate` affiche sur les scénarios du banc est un **contrôle
de bon fonctionnement, pas une mesure** : ce qu'on y lit est en grande partie le
déploiement que nous avons écrit nous-mêmes dans ces scénarios. Le tableau est
étiqueté comme tel dans la sortie de la commande, pour qu'il ne soit jamais cité
comme un résultat.

## La dispersion se publie avec la moyenne

Un rôle qui va partout ne doit pas passer pour un rôle qui tient un poste. Chaque
profondeur est donnée avec son écart-type, et un test vérifie qu'une position
très variable ressort comme telle. Une moyenne publiée seule ferait d'une unité
sans place assignée une unité disciplinée.

## Ce que cela ne donnera pas

**Le relief.** Une unité peut se tenir en retrait parce que c'est tactiquement
bon, ou parce qu'une colline l'y oblige. Nos données ne permettent pas de
trancher, et aucune moyenne ne le fera. Tout au plus l'altitude enregistrée
depuis la révision 13 dira-t-elle un jour si les tireurs cherchent la hauteur —
et cela restera une corrélation.

## Décision

`totalwar-ai learn --targets` affiche désormais la formation observée à la suite
du ciblage : les deux se lisent ensemble, sur le même corpus.

Rien n'est branché sur l'agent. Une formation apprise ne remplacera nos
constantes que si elle fait mieux **en bataille réelle** — et le banc ne pourra
pas en décider, puisque la doublure n'a pas de formation à imiter.

**Un instrument dont on connaît la limite vaut mieux qu'un instrument dont on
suppose la portée.**

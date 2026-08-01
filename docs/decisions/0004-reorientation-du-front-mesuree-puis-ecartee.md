# 0004 — Réorientation explicite du front : mesurée, puis écartée

- **Statut :** acceptée (fonctionnalité retirée)
- **Portée :** `totalwar_ai.agent.planner`

## Contexte

`REORIENT_FRONT` figure dans l'espace d'actions du `README.md`, et le
simulateur majore de 50 % les dégâts reçus dans l'arc arrière. Il paraissait
donc évident que l'agent devait faire pivoter ses unités vers une menace
arrivant de côté. Un réglage `reorient_angle_degrees` existait d'ailleurs dans
`PlannerSettings` sans que rien ne l'utilise.

## Mesure

La fonctionnalité a été implémentée puis comparée à son absence, à scénario et
graine identiques, sur trois graines :

| Seuil de pivot | `balanced_clash` | `outnumbered` | `ranged_defense` |
| --- | ---: | ---: | ---: |
| 60° | 36 % / 389 ordres | **30 %** / 192 ordres | 83 % / 97 ordres |
| 100° | 36 % / 388 ordres | **30 %** / 192 ordres | 83 % / 97 ordres |
| 140° | 36 % / 384 ordres | **30 %** / 192 ordres | 86 % / 95 ordres |
| désactivé | 33 % / 210 ordres | **46 %** / 136 ordres | 87 % / 95 ordres |

(forces alliées restantes en moyenne / nombre d'ordres émis)

`outnumbered` passe systématiquement du match nul à la défaite, quel que soit le
seuil, et le volume d'ordres augmente de 40 à 85 % pour un gain nul ailleurs.

## Décision

Ne pas émettre `REORIENT_FRONT`. Le réglage `reorient_angle_degrees`, qui ne
servait plus, est supprimé plutôt que conservé « au cas où ».

Ce qui est **conservé** de l'expérience : en posture défensive, une unité tient
désormais sa position face au barycentre des ennemis proches pondéré par leur
puissance, et non face à l'axe général du plan. Cette partie-là est neutre en
mesure et correcte en intention — on fait face à la masse, pas à l'unité la plus
proche, qui peut être une cavalerie isolée pendant que le gros charge de face.

## Pourquoi le gain espéré n'existe pas

Dans le simulateur, une unité au contact se tourne d'elle-même vers son
adversaire le plus proche à chaque tick. Le pivot explicite ne pouvait donc
agir qu'avant le contact, pour un bénéfice marginal — payé par un ordre
supplémentaire, la remise à zéro de l'ordre courant de l'unité, et un budget
d'ordres par minute entamé.

`ActionType.REORIENT_FRONT` reste dans le protocole et reste exécutable par
l'adaptateur : un futur planificateur, ou le jeu réel dont le modèle de
facing sera différent, pourra en avoir besoin. C'est la doctrine actuelle qui
ne l'emploie pas, pas le protocole qui l'interdit.

## Conséquence de méthode

Une intuition tactique plausible peut dégrader l'agent. Le banc de scénarios
sert précisément à le voir : toute doctrine ajoutée devrait être comparée à son
absence avant d'être conservée.

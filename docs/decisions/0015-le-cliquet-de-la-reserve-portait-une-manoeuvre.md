# 0015 — Le cliquet de la réserve portait une manœuvre

**Statut :** retenu — défaut supprimé, manœuvre rendue délibérée, banc inchangé
à 82 % et forces restantes 78 % → 80 % — 16/08/2026

## Le défaut, et son mécanisme

Sur `skirmish_standoff`, face à un ennemi **parfaitement immobile**, l'ancre du
dispositif reculait de 198 m, l'infanterie parcourait 300 m *en s'éloignant*, et
la réserve changeait de composition **119 fois en 1201 plans**, entre les quatre
mêmes unités.

Ce n'était pas un hasard. Deux boucles s'enchaînaient, et chacune est lisible en
clair dans le code.

**Boucle 1 — le critère de sélection éliminait celui qu'il venait de choisir.**
`build_groups` prenait comme réserve les unités de ligne les plus fortes au sens
d'`effective_strength`. Cette mesure inclut la fatigue, et le simulateur fait
payer le simple déplacement. L'unité mise en réserve reculait de soixante mètres,
**se fatiguait par ce seul recul**, repassait derrière une unité restée immobile,
et se faisait remplacer au plan suivant.

**Boucle 2 — l'ancre suivait celui qui reculait.** La réserve est un groupe de
doctrine, pas un rôle : ses unités gardaient leur rôle de mêlée et comptaient
donc dans `_line_anchor`. On les envoyait derrière, l'ancre suivait, le point de
ralliement reculait encore.

L'ironie est que la docstring de `_line_anchor` décrivait déjà exactement cette
boucle — *« replier les tireurs déplace le centre vers l'arrière, ce qui replie
encore les tireurs »* — et se croyait protégée parce qu'elle excluait les rôles
de tir. La réserve la rouvrait par une autre porte.

## La correction

* **Appartenance collante** : `_reserve_ids` sur le planificateur, honoré par
  `build_groups`, qui ne pourvoit que les places rendues vacantes. Aucune unité
  n'est remplacée pour cause de fatigue.
* **L'ancre écarte la réserve** : elle devient le centre de la ligne *qui
  combat*. Ce n'est pas figer l'ancre — la correction que l'ADR 0005 a mesurée
  et rejetée — puisqu'elle continue de suivre la ligne partout où celle-ci va.

Mesure : recul de l'ancre **198 m → 19 m**, changements de composition
**119 → 1**.

> La boucle 1 portait l'essentiel : l'appartenance collante seule ramène le
> recul à 24 m. L'exclusion de l'ancre ne gagne que les cinq derniers mètres. Le
> diagnostic initial attribuait les deux moitiés à parts égales ; c'était faux.

## Ce que la correction a révélé, et qui vaut plus qu'elle

Le cliquet supprimé, `balanced_clash` est tombé de **12 victoires sur 12 graines
à 5 sur 12**.

L'attribution des dégâts explique pourquoi. En traînant toute la formation vers
l'arrière, le cliquet produisait un **repli tirant** : l'ennemi suivait, restait
sous le feu, et les archers vidaient leur carquois.

| | pertes ennemies | infligées ligne hors contact | munitions |
| --- | --- | --- | --- |
| avec le cliquet | 6,91 | **60 %** | 2,00 / 2,00 |
| sans | 4,90 | 51 % | **1,53 / 2,00** |

**Le banc gagnait sur un accident.** Personne n'avait voulu cette manœuvre, rien
ne la documentait, et elle valait sept victoires sur douze.

Deux corrections en découlent.

**Fidélité du simulateur.** `_resolve_missiles` faisait taire toute unité sous
ordre `RETREAT`. Or `RETREAT` et `MOVE_GROUP` se traduisent par **la même
commande** vers le jeu (`OrderTranslator.destination_keys`) : aucune bataille
réelle ne peut distinguer les deux. Le simulateur modélisait une différence qui
n'existe pas, et en pénalisait l'agent. Seul le contact fait désormais taire un
tireur.

**Repli tirant délibéré.** La manœuvre est reprise à dessein, avec quatre
conditions qui sont toutes des conditions d'**arrêt** : posture défensive,
munitions restantes, personne au contact, ennemi effectivement proche
(`withdraw_trigger`). C'est ce qui la sépare des deux corrections rejetées par
l'ADR 0005. Déclencher à 110 m au lieu de 66 fait tomber le taux de 6/12 à 0/12,
l'ennemi finissant à 41 % au lieu de 25 % : la manœuvre ne vaut que si elle garde
l'adversaire sous le feu.

Résultat : `balanced_clash` revient à **11 victoires sur 12 graines**.

## Deux défauts trouvés en chemin

* **Les tireurs ne suivaient jamais la ligne.** Le pipeline tactique ne
  repositionnait pas le groupe de tir : un tireur recevait un ordre de repli s'il
  était menacé, un ordre de tir s'il avait une cible à portée, et **rien du tout**
  sinon. Tant que l'ennemi venait à nous, cela ne se voyait pas. Dès que la ligne
  avance, elle arrive seule : mesure sur `skirmish_standoff`, l'infanterie
  parcourt 165 m pendant que les deux unités de tir bougent de 5,5 m et 1,6 m.
  C'est mot pour mot ce que l'opérateur avait décrit en jeu — « trois unités
  d'archers qui n'ont pas suivi le pack ».
* **L'état du planificateur fuyait d'une bataille à l'autre.**
  `DeterministicTacticalAgent.reset` ne vidait ni `_commitments` ni la
  composition de la réserve.

## Ce qui a été mesuré puis écarté

Contre un ennemi qui n'avance jamais, l'agent attend indéfiniment : 100 % contre
100 % après six cents secondes, sans un coup tiré. Une règle d'initiative a été
écrite et mesurée sous trois formes. **Aucune ne gagne :**

| manœuvre | issue sur `skirmish_standoff` |
| --- | --- |
| ne rien faire | nul, 100 % / 100 % |
| avancer les seuls tireurs à portée | nul, 61 % / 64 % |
| avancer toute la formation à portée | défaite, 63 % / 87 % |
| charger d'emblée | défaite, 61 % / 100 % |

La deuxième est en outre tactiquement fausse : elle envoie les tireurs 112 m
devant la ligne, sans escorte, face à un adversaire qui a de la cavalerie —
exactement la défaite en détail que `CONCENTRATION_WEIGHT` existe pour empêcher.

Le code a donc été retiré plutôt que laissé en place. Ce que le scénario demande
n'est pas « avancer » mais **concentrer l'assaut sur une partie de leur ligne**,
manœuvre que l'agent ne sait pas conduire. C'est le travail suivant, et il ne se
règle pas avec un seuil.

## Ce que cela ne prouve pas

Le monde du banc est plat (altitudes `{0.0}`, vérifié) et sans moral. Le repli
tirant y vaut ce qu'il vaut ; en jeu, il dépendra du terrain et de la vitesse
réelle des unités, dont aucune n'est modélisée ici. La mesure qui tranchera reste
le journal de `--play`.

**Les deux scénarios à 0 % le restent.** Le critère « 100 % au banc avant de
tester » n'est pas atteint, et rien dans cet ADR ne prétend l'atteindre.

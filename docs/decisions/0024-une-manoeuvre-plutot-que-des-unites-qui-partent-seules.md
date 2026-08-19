# 0024 — Une manœuvre, plutôt que des unités qui partent seules

**Statut :** retenu pour les étapes 1 à 4, dispositif complet — le retrait de la
branche opportuniste est **mesuré et reporté** — 19/08/2026

## Le défaut, et sa branche de code

Le 18/08 à 22h20, en posture `DEFEND`, trois unités entrent au combat entre
192,6 s et 241,6 s, puis plus personne pendant 235,5 s. Ces trois-là :

| unité | type | `missile_range` |
| --- | --- | ---: |
| 1010 | `cav_jade_lancers` | 0,0 |
| 1011 | `cav_jade_longma_riders` | 0,0 |
| 1012 | `veh_sky_lantern` | **275,0** |

Ce sont exactement les trois unités de `MOBILE_ROLES` de l'armée. Les quatre
infanteries et les quatre arbalétriers sont restés. Ce n'était pas une
particularité de bataille, c'était une branche : `_command_cavalry` faisait partir
une unité rapide dès qu'un tireur adverse était exposé, sans qu'aucune décision
d'armée ne relie ce départ au `HOLD` de la ligne.

**Le défaut n'est pas qu'une petite force agisse seule.** `numerical_superiority`
gagne ses trois batailles avec une seule unité au contact. Le défaut est qu'une
force agisse seule *alors que le plan global exige implicitement que le reste la
soutienne*.

## L'autorité, jamais une distance

Un seuil du type `if distance > 150: interdit` serait une rustine : un flanc à
200 m est valide si le paquet avance avec lui, un flanc à 70 m est suicidaire si
la cavalerie est seule. La question posée est donc :

```
réaction défensive locale        →  autorisée hors manœuvre
ouverture offensive d'un combat  →  exige une MANOEUVRE active
```

Et la permission de passivité change d'objet : elle ne donne plus aux **unités**
le droit de partir, elle donne au **général** le droit de composer une manœuvre.

## La mission vient de la capacité, pas du rôle générique

Première règle écrite : « mobile donc flanqueur ». Le roster ci-dessus la
condamne — le Sky Lantern porte la portée exacte des Crane Gunners, une unité de
tir dédiée. `FLYING_UNIT ∈ MOBILE_ROLES` mais `∉ RANGED_ROLES` : cette règle en
aurait refait un flanqueur et l'aurait envoyé au contact, c'est-à-dire lui aurait
réattribué la mission qui l'a tué.

Le signal existait, vérifié en bataille sur un prince démon : `missile_range` vaut
0 sur qui ne tire pas. Une plateforme de tir appuie, même volante ; une unité
rapide qui ne tire pas flanque ; le reste porte le choc.

En passant, le `conftest` donnait 120 m de portée à **toutes** les unités de test,
mêlée comprise — un corps de bataille que le jeu ne produit jamais, et qui
masquait entièrement la question.

## Trois verrous trouvés par la mesure, tous introduits par ce chantier

**Un rassemblement sans sortie.** Avant de brancher `ASSEMBLE`, une mesure a
montré que *zéro* manœuvre atteignait `CONTACT` : 27 plans, tous en rassemblement.
Retenir les participants sans borne aurait remplacé « trois unités partent
seules » par « douze unités n'agissent jamais ». D'où l'abandon sur
`ASSAULT_DEADLINE` — une borne qui existait déjà et dit exactement cela.

**Une readiness qui reculait.** Sur `numerical_superiority`, le cavalier atteint
sa position exacte à 30 s et devient prêt ; l'ennemi vient à lui à 40 s, et sa
readiness repasse à faux **définitivement**. La clause « en place et pas au
contact » visait le flanqueur parti en avance — mais `ASSEMBLE` l'empêche
désormais structurellement de partir, donc un flanqueur au contact pendant le
rassemblement s'est fait attaquer.

**Une exigence que personne ne pouvait satisfaire.** Le seigneur entre dans
`attackers`, donc il était requis — alors que `_command_leaders` le maintient
« hors de la mêlée frontale » et qu'aucun chemin ne l'amène à une position de
départ. Il bloquait **les trente plans de rassemblement du banc**, seul. N'est
requis désormais que ce que la doctrine envoie vraiment au contact.

Après ces trois corrections : **40 % des plans à manœuvre atteignent `CONTACT`**,
contre 9 %.

## Le paquet ne bougeait qu'au tiers

Une fois `ASSEMBLE` branché, la question restait entière : **tout le dispositif
rejoint-il sa position, ou seulement le paquet de choc ?** La mesure a répondu.

| rôle | envoyé à sa position | après correctif |
| --- | ---: | ---: |
| `flank` | 100 % | 100 % |
| `fix` | 86 % | 86 % |
| `assault` | 32 % | **66 %** |
| `fire_support` | **0 %** | **27 %, plus 66 % qui tirent** |

`_stage` n'était appelé que depuis la ligne et la cavalerie. Les deux groupes
qu'il ne traversait jamais sont exactement ceux qui manquaient.

**Les tireurs restaient en arrière.** Les `FIRE_SUPPORT` appartiennent aux groupes
`MISSILE` et `ARTILLERY`, traités par `_fire_missiles` — lequel savait déjà
rattacher les tireurs sans cible, mais sur **l'ancre**, pas sur le secteur de la
manœuvre, et seulement en posture offensive. Cent vingt-six participants sur cent
vingt-six n'ont jamais reçu d'ordre de rassemblement. C'était « trois unités
d'archers qui n'ont pas suivi le pack » — le même défaut, cette fois pendant la
manœuvre.

Corrigé, plus aucun appui-feu n'est muet pendant le rassemblement : 66 % tirent,
ce qui **est** l'appui, 27 % rejoignent leur portée, 7 % se replient ou se
protègent. `_protect_ranged` garde la priorité — rassembler un tireur qu'une
cavalerie charge reviendrait à l'y envoyer mourir pour tenir un horaire.

**Le seigneur portait un rôle qu'il ne tiendrait jamais.** Quatre-vingt-quatre des
participants `ASSAULT` non rassemblés étaient dans le groupe `COMMAND`, dont
`_command_leaders` ne produit qu'un seul ordre : reculer vers un point de soutien.
Il n'attaque jamais. Le commandement ne reçoit donc plus de rôle de manœuvre — il
a sa propre doctrine, et la télémétrie cesse d'annoncer un assaillant qui
n'assaillira pas. Les 33 `ASSAULT` restants sont déjà au contact : les décrocher
les ferait tuer de dos.

La séquence visée est désormais lisible dans la télémétrie :

```
ASSEMBLE   fixation et flanc rejoignent leur position
           l'appui tire, ou se met en portée
              ↓
CONTACT    le flanc charge, la fixation tient, l'appui rejoint son poste
```

Le `HOLD` de la fixation n'est plus une absence : c'est un rôle.

**Effet mesuré** : `outnumbered` remonte de trois défaites à **une défaite et deux
nuls**, au-dessus de ce que le code donnait avant ce chantier ;
`numerical_superiority` passe de 87 % à **94 %** de forces restantes, en 37 s au
lieu de 55 s.

## Le retrait de la branche opportuniste est reporté

C'est la conclusion principale de cet ADR, et elle est négative.

Cette branche porte **933 des 964 flanquements du banc — 97 %** — dont 897 sur le
seul `skirmish_standoff`. Avant de la retirer, il fallait prouver que la manœuvre
avait repris sa fonction d'initiative. La mesure dit le contraire :

| scénario | avec la branche | sans elle |
| --- | --- | --- |
| `skirmish_standoff` | 3 nuls | **3 défaites** |
| `balanced_clash` | 3 victoires | **3 nuls** |
| `ranged_defense` | 3 victoires | 3 victoires |
| `cavalry_flank_threat` | 3 victoires | 3 victoires |

La raison est nette. Sur `skirmish_standoff`, 180 plans, tous en `DEFEND` : la
permission de passivité est accordée **168 fois**, et `commit()` échoue **168
fois** (`COMMIT_FAILED`). Le général a le droit de composer une manœuvre et
n'y arrive jamais — aucun secteur n'atteint le rapport exigé.

C'est le même mur que l'ADR 0020 a mesuré sur les secteurs, et que la fenêtre
glissante de l'ADR 0019 avait cru contourner avant de transformer le nul en
défaite. **La branche opportuniste est aujourd'hui le moteur d'initiative de fait
du banc**, et la retirer maintenant retirerait l'initiative sans la remplacer.

Elle reste donc en place. Le chantier suivant n'est pas de la supprimer, mais de
comprendre pourquoi aucun secteur ne compose en posture défensive.

## Un banc qui ne voyait pas ce qu'il perdait

La comparaison ne regardait que le taux de victoire, les forces restantes et la
survie du seigneur. Sur `outnumbered`, la référence porte trois nuls ; le code
d'**avant** ce chantier en donnait déjà deux défaites et un nul. La chute
précédait donc ce travail, et rien ne l'avait jamais signalé, parce que 0 % de
victoires reste 0 % de victoires.

Le dispositif complet a depuis ramené le scénario à une défaite et deux nuls,
c'est-à-dire **au-dessus** de l'état où ce chantier l'a trouvé. L'écart qui reste
avec la référence lui est entièrement antérieur.

La part de batailles non perdues est désormais comparée comme les autres
métriques. Ce sont précisément les scénarios bloqués à zéro victoire qui portent
tout le travail restant : y perdre un nul doit se voir.

## Ce que la manœuvre laisse ouvert

`ASSAULT_ROLES` compte tout rôle hors `RANGED_ROLES`, donc la force du Sky Lantern
entre dans le rapport local comme de la force de mêlée — alors que le module dit
lui-même que « compter une force qui ne vient pas est exactement le défaut que
l'archer a révélé ». Le corriger déplacerait quels secteurs sont jugés faisables,
donc le banc, et confondrait deux mesures. Le défaut est **antérieur** à ce
chantier ; il est consigné ici plutôt que tu.

Le banc, enfin, ne compte que six manœuvres sur 33 batailles. Comme pour LIVE-001
et LIVE-002, il ne pourra pas arbitrer ce chantier : seule une session réelle le
peut, et son critère n'est pas un nombre d'unités en mêlée mais la question
« les participants d'une même manœuvre tiennent-ils leurs rôles dans une
chronologie coordonnée ? ». La télémétrie la rend lisible après coup — qui
attendait qui, quand le contact a été autorisé, quel participant manquait.

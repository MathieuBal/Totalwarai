# 0007 — Fiabiliser l'observation avant de constituer le corpus

**Statut :** livré, en attente du premier essai en bataille — 03/08/2026

## Pourquoi maintenant

L'objectif retenu est d'apprendre en regardant jouer l'IA du moteur, jusqu'à
pouvoir s'en passer. Cela demande un corpus de plusieurs dizaines de batailles
réelles — donc des heures de jeu qui ne se rattrapent pas.

**C'était le dernier moment** où corriger la chaîne d'observation ne coûtait
aucune partie jouée. L'audit y a trouvé trois défauts, tous silencieux.

## Les trois défauts

### On perdait des états

`FileBridge.latest_battle_state()` vidait le flux et ne rendait que le dernier.
Le jeu publie chaque seconde, la boucle dort une seconde — mais en supervision
l'attente d'un accusé dure jusqu'à deux secondes, et les états intermédiaires
disparaissaient définitivement.

Passée la publication à 2 Hz, c'eût été **la moitié du corpus perdue**.

`LiveStep` porte désormais tous les états lus ; l'enregistrement les garde tous.
La fréquence d'observation cesse d'être celle de décision : voir coûte moins
cher qu'agir.

### On ne pouvait pas s'en apercevoir

L'enregistrement notait son propre compteur de tours, jamais le `sequence` de la
sonde. Un trou était donc indétectable, et rien ne distinguait une bataille
exploitable d'une bataille trouée. La séquence est enregistrée.

### On jetait l'altitude

`unit:position():get_y()` répond en bataille — 21 à 33 relevés dès l'essai n° 3
— et `recording.py` ne gardait que `x` et `z`. C'est la seule donnée de terrain
que le jeu nous donne, et elle partait à la poubelle à chaque tour.

## Le terrain n'est pas une case fermée

La fiche de faisabilité portait « aucune donnée de terrain ». C'est vrai des
accesseurs d'unité recensés, et **faux du reste**.

Outre l'altitude sous chaque unité, `v_to_ground(v(x, 0, z))` projette un point
**sur le sol** — et notre propre code l'appelle déjà à chaque ordre de
déplacement. Si le vecteur rendu expose son `get_y()`, nous tenons une sonde
d'altitude en tout point de la carte, et un relief complet devient calculable
avant le premier coup de feu.

La révision 13 échantillonne une croix de cinq points et journalise les
altitudes. **Des valeurs qui diffèrent prouveront que la sonde lit le relief** ;
des valeurs identiques diraient qu'elle rend une constante.

Rien n'est bâti sur cette hypothèse. Construire avant de savoir a déjà coûté
trois essais à ce projet — `math.huge`, `unary_morale`, `number_of_men`.

## La décision fantôme

En `--observe`, notre agent décide désormais **dans le vide** : rien ne part
vers le jeu, et chaque tour devient un couple étiqueté « l'IA a fait ceci, nous
aurions fait cela ». C'est le signal d'apprentissage le moins cher qui existe,
et nous le jetions.

Les règles de supervision sont évaluées en parallèle par un superviseur d'ombre.
On saura donc à quelle fréquence chacune se déclencherait en vraie bataille :
`artillerie_au_contact` n'a jamais rien déclenché au banc, et cela dira si c'est
le banc qui manque de cas ou la règle qui ne sert à rien.

La garantie de ne rien émettre tient par construction — ni l'agent ni la
traduction ne touchent au pont — mais elle est trop importante pour reposer sur
la lecture du code : un test compte les ordres publiés pendant une session
d'observation et exige zéro.

## L'inférence des décisions, et son étalonnage

Le jeu ne dit pas quel ordre porte une unité. `learning/observation.py` le
reconstitue de deux états successifs : engager, foncer sur, tirer sur, tenir,
décrocher. **Le tir se trahit par la baisse des munitions** — seul signal
disponible, aucune API ne disant qu'une unité tire ni sur qui.

### Elle dit quand elle ne sait pas

Deux ennemis également approchés ne permettent pas de dire lequel était visé.
La cible est alors laissée vide plutôt que tirée à pile ou face, et le taux
d'ambiguïté est publié. Une inférence sûre d'elle sur des données ambiguës
serait pire qu'inutile.

### Elle est étalonnée sur une politique connue

La doublure de `simulation/native_ai.py` a une politique **connue exactement** :
la cavalerie préfère les tireurs, la mêlée prend le plus proche. On lui fait
jouer une bataille et l'on vérifie que l'inférence retrouve cela.

Si elle échouait sur une politique dont on connaît la réponse, elle ne dirait
rien de bon sur l'IA du jeu — et cela se vérifie **sans jouer une seule
bataille**. Ce n'est pas circulaire : on n'apprend aucune tactique de la
doublure, on étalonne l'instrument.

### Ce que la mesure donne

`totalwar-ai learn --calibrate` reproduit cette mesure d'une seule ligne. Les
chiffres ci-dessous en sortent ; ceux publiés d'abord ici — 27 % et 21 % —
venaient d'un script jeté après usage, ce que la conduite adoptée en ADR 0006
interdit. L'écart tient à la durée des scénarios rejoués, pas à un changement
d'algorithme.

| | Décisions ciblées | Ambiguës |
| --- | --- | --- |
| sans continuité | 2200 | **29,9 %** |
| avec continuité | 2200 | **22,6 %** |

La continuité — une unité qui frappait un ennemi et l'a toujours au contact le
frappe encore — n'est pas une supposition mais la propriété la plus stable d'un
ordre, et elle ne désigne jamais qu'un adversaire déjà candidat et encore
proche.

Le gain est réel et modeste. **L'essentiel de l'ambiguïté restante tient au tir
en mêlée compacte**, où plusieurs ennemis sont à distance égale et où nos
données ne permettent pas de trancher. C'est une limite des données, pas de
l'algorithme, et elle ne se lèvera pas par du code.

## Savoir sur quoi on apprend

`learn --check` dit, bataille par bataille, ce que valent les enregistrements :
états, décisions, trous, issue connue ou non, unités, décision fantôme. Trois
exigences à poids égal donnent un taux de complétude.

Le premier lancement a trouvé **415 batailles**. C'étaient les journaux du
simulateur, qui partagent le répertoire et l'extension sans rien partager du
format. Chaque enregistrement réel porte désormais un en-tête qui le nomme.

## Décision

Aucune partie n'est jouée avant que ceci ne soit en place. La première
escarmouche sert de contrôle : `learn --check` doit afficher une bataille
complète avant d'en enchaîner vingt-neuf autres.

**Une bataille de contrôle avant trente vaut mieux que trente à refaire.**

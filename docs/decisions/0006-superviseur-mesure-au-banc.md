# 0006 — La supervision se mesure au banc, et deux règles y ont échoué

**Statut :** outil livré, trois mesures faites, une règle retenue et une rejetée — 02/08/2026

## Le problème

Après onze essais en bataille, `--supervise` n'avait **rien démontré**. Chaque
règle coûtait une bataille réelle à l'opérateur, et le seul essai qui a tourné
n'a produit aucune intervention — l'armée était en mêlée pure, aucune des trois
règles ne pouvait se déclencher.

Une supervision qu'on ne peut pas mesurer ne s'améliore pas.

## L'outil

`simulation/native_ai.py` fait jouer **nos** unités par la politique scriptée
qui mène déjà l'adversaire depuis onze scénarios : avancer, tirer à portée,
lancer la cavalerie sur les tireurs. La décision par unité est partagée entre
les deux camps — deux copies divergeraient, et la mesure ne vaudrait plus rien.

`run_supervised_battle` joue une bataille menée par cette doublure, que le
`Supervisor` de `bridge/supervision.py` supervise **tel quel**. C'est le même
code qui est mesuré et expédié.

`bench --supervised` compare doublure seule et doublure supervisée à graines
identiques, avec la détection de régression existante.

> **La doublure n'est pas l'IA du jeu.** Elle ignore le terrain, les formations
> et le pathfinding. C'est un filtre rapide, pas un juge — l'ADR 0005 documente
> deux corrections que le simulateur a validées et que le jeu a démenties.

## Ce que la mesure a dit

Onze scénarios, trois graines, trente-trois batailles par jeu de règles.

| Jeu de règles | Victoires | Forces | Survie du seigneur | Reprises |
| --- | --- | --- | --- | --- |
| aucune | 30/33 | 71 % | 76 % | 0 |
| `seigneur_en_danger` seul | 30/33 | 70 % | **88 %** | 12 |
| `artillerie_au_contact` seul | 30/33 | 71 % | 76 % | **0** |
| `tireur_au_contact` seul *(version d'origine)* | **27/33** | 69 % | 76 % | 27 |
| détresse, après correction | 30/33 | 70 % | **88 %** | 15 |
| détresse + `tireur_isole` | **28/33** | 69 % | 88 % | 73 |

### `seigneur_en_danger` est gardée

Douze points de survie du seigneur gagnés sans perdre une victoire. C'est le
seul gain net du jeu de règles.

### `artillerie_au_contact` n'est ni gardée ni rejetée

Elle ne s'est **jamais déclenchée** : aucune pièce d'artillerie n'entre en mêlée
dans ces onze scénarios. Elle reste en place, non mesurée. Une règle qui ne se
déclenche pas ne peut pas nuire, mais il ne faut pas la présenter comme validée.

### `tireur_au_contact` était nuisible, et on sait pourquoi

Elle portait à elle seule toute la régression : 30/33 → 27/33 victoires.

Quatre variantes ont été mesurées pour comprendre. Ni une distance de repli plus
courte (35 m au lieu de 90), ni un seuil de santé, ni une limite au nombre
d'assaillants ne changeaient quoi que ce soit. **La condition de munitions
restaure 30/33** et ramène les reprises de vingt-sept à trois.

L'explication tient en une phrase : un tireur à court de munitions n'est plus
qu'une unité de mêlée médiocre. Le dégager ne lui rend aucune valeur, ouvre un
trou dans la ligne, et il se fait rattraper en chemin.

### `tireur_isole` est rejetée

Première règle d'**occasion** tentée — ramener un tireur séparé de la ligne de
mêlée, transposée de la doctrine d'Harmonie du Grand Catay. Mesure : 30/33 →
**28/33** victoires, pour soixante-treize reprises.

Elle est retirée du code. Le raisonnement était bon ; le résultat ne l'est pas.
Soixante-treize reprises sur trente-trois batailles, c'est une supervision qui a
cessé de superviser — elle rejoue le placement d'une IA qui le fait mieux.

## Erreur de mesure, et ce qu'elle enseigne

Une exécution intermédiaire a annoncé `balanced_clash` passant de 67 % à 100 %
de victoires grâce aux règles de détresse. **C'était faux, et dans le mauvais
sens** : la mesure isolée, refaite trois fois, donne 3/3 sans règle et 2/3 avec.
Le commit `39c0fd9` reprend ce chiffre erroné.

Le chiffre juste est plus modeste et reste bon : les règles de détresse ne
coûtent **aucune** victoire par rapport à l'absence de supervision, et gagnent
douze points de survie du seigneur.

Règle de conduite qui en découle : **un verdict de banc se relit deux fois avant
d'être écrit dans un commit**, et un écart surprenant se vérifie par une mesure
isolée avant d'être annoncé.

## Décision

Sont conservées les trois règles de détresse, dont deux mesurées utiles et une
jamais déclenchée. Est rejetée la première règle d'occasion.

**Toute règle suivante passe par `bench --supervised` avant d'entrer dans
`DEFAULT_RULES`**, et le verdict est relu avant d'être cité. Le juge final
reste la bataille réelle : le critère de réussite du chantier est que
`--supervise` batte `--observe` en jeu, sur plusieurs compositions d'armée.

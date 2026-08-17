# 0016 — Créer une supériorité locale, plutôt qu'être supérieur partout

**Statut :** retenu — la manœuvre existe et se mesure ; le banc reste à 82 % sans
régression, forces restantes 80 % — 17/08/2026

## Le problème

Face à une ligne adverse, l'agent ne savait que deux choses : avancer toute la
ligne, ou attendre toute la ligne. Les quatre formes d'initiative mesurées par
l'ADR 0015 contre un ennemi qui refuse d'avancer ont toutes échoué — attente,
tireurs seuls, avance escortée, charge directe : nul, nul, défaite, défaite.

Ce qui manquait n'était pas un seuil, c'était une capacité. Une armée n'a pas
besoin d'être supérieure partout au même instant. Le critère cesse donc d'être

    force_alliée_totale / force_ennemie_totale >= 1.0

et devient, sur une tranche du front choisie exprès,

    force_engagée / (force_du_secteur + soutien_qui_peut_venir) >= 1.5

## La primitive — `agent/sectors.py`

Découper le front adverse en trois tranches latérales, évaluer chacune, engager
le nécessaire sur la meilleure, **fixer ailleurs**, et réévaluer après la
rupture.

Trois choix méritent d'être justifiés.

**Le soutien est dans le dénominateur.** Un secteur faible collé au gros de leur
ligne n'est pas faible : c'est un appât. Sans ce terme, la primitive irait
systématiquement au détachement le plus isolé — c'est-à-dire, souvent, là où l'on
se ferait envelopper.

> Le soutien se mesure depuis **les membres du secteur**, pas depuis son centre.
> Une tranche fait plus de cent mètres de large et `SUPPORT_RADIUS` en fait
> quarante : mesuré depuis le centre, le terme aurait été vide presque toujours —
> du code mort qui aurait eu l'air de protéger. C'est le test de l'appât qui l'a
> montré, avant que la primitive ne soit branchée.

**Le numérateur ne compte que ce qui peut arriver.** Une unité à trois cents
mètres ne concentre rien : elle arrive après. `REACH` la retire du compte.

**L'appartenance est collante.** Un secteur rechoisi à chaque plan reproduirait
le défaut de la cavalerie qui recevait dix cibles de contournement en cent trente
secondes (ADR 0013). L'assaut n'est relâché qu'à la rupture — et rompre n'est pas
poursuivre : le choix recommence sur l'état du moment.

## Où elle s'applique, et où elle se tait

Trois conditions d'abstention, toutes mesurées :

| condition | ce que sa violation a coûté |
| --- | --- |
| pas pendant un **repli tirant** | `balanced_clash` 100 % → 0 %, forces 33 % → 21 % |
| pas en posture **`DEFEND`** | `balanced_clash` 100 % → 0 %, forces 33 % → 20 % |
| pas une fois les lignes **au contact** | redistribuer une ligne en mêlée laisse des unités libres immobiles |

`DEFEND` est la posture de celui qui a l'avantage du feu : sa manœuvre gagnante
est d'attendre puis de reculer en tirant, et y lancer un assaut revient à aller
chercher la mêlée qu'on cherchait à différer.

`DELAY` est l'inverse et ne devait surtout pas être traitée pareil : **on y est
inférieur globalement, et battre en détail y est le seul chemin.**

## Ce que la mesure dit

Chiffres produits par la boucle de décision réelle — `sector_assaults`,
`assault_best_ratio` — et non par un rejeu a posteriori, qui n'aurait pas la
mémoire qu'avait le planificateur à cet instant.

| scénario | rapport **global** | assauts | rapport **local** |
| --- | --- | --- | --- |
| `cavalry_flank_threat` | 1,25 | 6 | **1,99 – 2,13** |
| `numerical_superiority` | 2,67 | 3 | 2,00 |
| `rout_pursuit` | 10,00 | 3 | 2,08 |
| `outnumbered` | **0,67** | 3 | **1,50** |

La dernière ligne est celle qui compte : **inférieur de moitié globalement, et
malgré tout supérieur de moitié quelque part.** C'est exactement ce que la
primitive existe pour faire.

La fenêtre est étroite et se referme. Sur `outnumbered`, deux secteurs offrent
1,50 et 1,89 aux trente premières secondes ; vers la centième, leur ligne s'est
ressoudée, le terme de soutien apparaît, et tous les rapports tombent sous 0,70.

## Ce que cela ne fait pas

**Le banc reste à 82 %, et les deux scénarios à 0 % le restent.** La manœuvre
crée la supériorité locale ; elle ne la convertit pas encore en victoire sur
`outnumbered`, et ne se déclenche pas du tout sur `skirmish_standoff`, dont la
posture est `DEFEND`.

Le test qui accompagne cet ADR l'assume : il **n'exige aucune victoire**. Un taux
de victoire ne distingue pas « la manœuvre ne se déclenche jamais » de « elle se
déclenche et ne change rien » — les deux rendent le même chiffre. Le test sépare
les deux, et c'est tout ce qu'il prétend faire.

Ce qui manque pour convertir est vraisemblablement ailleurs : rompre un secteur à
1,50 contre un adversaire qui converge demande d'y arriver vite, donc de la
mobilité employée en enveloppement — pas d'un seuil de plus.

## Les trois portes

La règle qui les accompagne compte autant que les chiffres.

```
GATE A — banc fixe (11, 23, 37)
  aucun scénario à 0 %, aucune régression
  un nul contre un ennemi 100 % passif n'est pas un échec,
  à condition qu'une supériorité locale ait été mesurée
```

```
GATE B — graines réservées 9001–9012, via `bench --hidden`
  ≥ 90 % de victoires, aucune famille à 0 %,
  aucune chute importante de forces restantes
```

```
GATE C — Warhammer III
```

**Les graines 9001–9012 ne se jouent jamais pendant le développement** — ni pour
régler un seuil, ni pour diagnostiquer, ni « juste pour voir ». Une graine qui a
servi à choisir n'est plus une graine de contrôle : elle mesure la recherche
qu'on a faite dessus. L'ADR 0013 a coûté la leçon — +4 points devenus +1 sur
graines inédites — et elle est désormais écrite dans le code, à côté de la
constante.

Elles sont volontairement hors de portée de `bench --seeds N`, qui prolonge la
série par 101, 102, … — plage déjà brûlée par ce même ADR.

**Gate A n'est pas franchie.** Rien ici ne prétend le contraire.

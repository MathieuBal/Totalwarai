# 0017 — Mesurer la conversion avant de la corriger

**Statut :** retenu — la mesure existe, elle a produit une réponse qu'aucune de
mes deux hypothèses ne couvrait — 17/08/2026

## Ce que j'avais affirmé sans le mesurer

En livrant la concentration locale (ADR 0016), j'ai écrit :

> Ce qui manque pour convertir n'est probablement pas un seuil mais de la
> **vitesse** — arriver sur le secteur avant qu'il ne se ressoude.

**Je n'avais mesuré ni la vitesse ni la conversion.** Le rapport local était
connu au moment du *choix* — 1,50 sur `outnumbered`, pour 0,67 global — et jamais
au *contact*. Sans ce second chiffre, deux diagnostics restent indiscernables :

* l'avantage **s'évapore** pendant l'approche → problème de mobilité, le
  correctif porte sur le coût d'affectation ;
* l'avantage **tient** et le secteur ne rompt pas → problème de ciblage, de
  concentration du feu ou d'achèvement, et surtout **pas** de vitesse.

Ils appellent des correctifs opposés. Coder l'un ou l'autre était un pari.

## L'instrument

`learning/assault.py` reconstitue chaque assaut du choix à son issue :
rapport au choix, au premier contact, quand la moitié des assaillants est
engagée, et le délai de chacun.

**Aucun événement dédié n'a été nécessaire.** `plan_selected` publiait déjà le
plan complet à chaque recalcul ; le plan porte désormais le rapport vivant et le
nombre d'assaillants au contact, et les phases se lisent dans la série. Médiane
plutôt que moyenne, refus de conclure sous cinq assauts — même discipline
qu'`elevation.py`.

### L'instrument a d'abord menti, et il a fallu le réparer

Premier passage sur `outnumbered`, douze graines :

```
assauts choisis           : 12
jamais arrivés au contact : 12

  L'avantage tient jusqu'au contact. Si le secteur ne rompt pas,
  la cause est en aval — et **pas** la vitesse d'arrivée.
```

**Le verdict disait l'inverse des données.** Sans contact, la part conservée vaut
`None`, « s'effondre » vaut faux, et la lecture tombait dans la branche
rassurante. Un instrument qui conclut sur une case vide est exactement ce que
cette mesure existait pour éviter. Un cas sans aucun contact a désormais son
verdict propre, et un test le pince.

## La troisième réponse

La mesure réparée disait : **douze assauts choisis, douze jamais arrivés.**

Ni mobilité ni conversion — la manœuvre n'avait pas lieu. Journal des plans :

```
t=  0.0s  CHOISI   posture=delay  rapport local 1,50
t= 20.0s  RELÂCHÉ  posture=delay  ennemi le plus proche : 12,1 m
```

Le **repli tirant annulait l'assaut**. L'ennemi passait sous `withdraw_trigger`
(66 m) au bout de vingt secondes, le repli se déclenchait, et l'assaut était
dissous avant d'avoir parcouru le moindre mètre.

C'est une interaction que j'avais introduite moi-même, en écrivant que « les deux
manœuvres se contredisent, et il faut choisir ».

## Elles ne se contredisent pas : c'est l'ordre oblique

Un assaut **commencé** n'est plus annulé par le repli ; simplement, aucun ne
**commence** pendant. Une partie de la ligne refuse le combat et recule sous le
feu, l'autre frappe là où nous sommes les plus forts.

Ce que la mesure précédente avait pris pour une incompatibilité était un assaut
*lancé pendant* un repli — jamais un assaut *poursuivi malgré* lui.

## Ce que la mesure dit maintenant

`outnumbered`, douze graines :

| | |
| --- | --- |
| rapport au choix | **1,50** |
| rapport au contact | **0,96** |
| part conservée | **64 %** |
| délai médian jusqu'au contact | **31 s** |

**L'avantage se volatilise pendant l'approche**, et l'assaut arrive *sous la
parité*. L'hypothèse « c'est la vitesse » se trouve confirmée — mais elle n'était
pas connue avant d'être mesurée, et elle aurait tout aussi bien pu être fausse.

Le correctif qui suit est donc justifié, et il ne l'était pas hier : un coût
d'affectation qui intègre le temps de trajet, pour ne composer l'assaut qu'avec
des unités capables d'arriver *ensemble* et *à temps*. Trente et une secondes
d'approche contre un adversaire qui se ressoude, c'est trop.

## Les contrats, réparés au passage

**Le contrat Gate A que j'avais écrit se contredisait** : il exigeait « aucun
scénario à 0 % » *et* admettait dans la même phrase qu'un nul contre un ennemi
passif ne soit pas un échec. L'ambiguïté se serait résolue, après coup, dans le
sens qui arrangeait le résultat.

Critère retenu, le plus strict des deux : **au moins une victoire par famille**.
Une supériorité locale mesurée valide la primitive qui la produit, jamais l'issue
de la bataille. Le verdict est rendu à chaque banc — une porte qu'on n'évalue
qu'au moment où l'on croit la franchir est une porte dont on choisit la date.

Deux autres trous fermés :

* **Trois pools réservés** (`9001+`, `9101+`, `9201+`) au lieu d'un. Une réserve
  lue est **brûlée** : si le code change après consultation, elle a servi à
  choisir, et le rejouer ne mesurerait plus que la correction qu'elle a inspirée.
* **Une CI** (`.github/workflows/ci.yml`). Jusqu'ici, tous les « tests verts » de
  ce projet étaient des résultats locaux que personne d'autre ne pouvait
  reproduire. Les graines réservées n'y tournent jamais : les publier à chaque
  proposition de changement reviendrait à les brûler.
* **Le chemin du pack.** `docs/feasibility.md` établit que `script/battle/mod/`
  donne `Failed to load mod` et que seul `script/_lib/mod/` charge — or la source
  vit exactement au chemin qui échoue. `scripts/build_pack.py` produit désormais
  le layout runtime, pour qu'on ne puisse plus fabriquer un `.pack` muet par
  distraction.
* **`docs/architecture.md`** affirmait qu'« aucune version future de notre code »
  ne donnerait accès au moral ni à la fatigue. C'était faux : le recensement
  avait essayé `unary_morale` et `fatigue`, quand le jeu documente
  `fatigue_state()` et `is_wavering()`. Une absence constatée sous un mauvais nom
  n'est pas une absence.

## Ce que cela ne fait pas

Le banc reste à **82 %**, forces restantes 80 %, aucune régression. **Gate A
n'est pas franchie** — `outnumbered` et `skirmish_standoff` restent sans aucune
victoire, et le verdict le dit maintenant à chaque exécution.

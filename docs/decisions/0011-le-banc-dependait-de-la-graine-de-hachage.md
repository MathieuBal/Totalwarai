# 0011 — Le banc dépendait de `PYTHONHASHSEED`

**Statut :** défaut trouvé, corrigé, gardé par un test — 07/08/2026

## Ce qui a été trouvé

Le banc ne rendait pas deux fois le même chiffre. Même scénario, même graine,
même configuration, deux processus :

```text
['victory', 'victory', 'victory']
['victory', 'victory', 'draw']
['victory', 'victory', 'draw']
```

En fixant `PYTHONHASHSEED`, le résultat redevenait stable — et changeait selon la
valeur fixée. `balanced_clash` à la graine 37 rendait « victoire » avec
`PYTHONHASHSEED=1`, « nul » avec `PYTHONHASHSEED=0`.

## La cause

`SimUnit.engaged_with` est un `set[str]`. `_resolve_melee` construisait sa liste
de défenseurs en itérant cet ensemble, puis appliquait les dégâts **défenseur par
défenseur**. L'ordre d'itération d'un ensemble de chaînes dépend du hachage, donc
de `PYTHONHASHSEED`, que Python randomise à chaque processus.

Cet ordre décide qui meurt en premier. Qui meurt en premier décide de la suite de
la mêlée. Un `min()` sur ce même ensemble, dans `_nearest_engaged`, départageait
les ex æquo de la même façon.

La correction tient en un `sorted()` aux deux endroits.

## Pourquoi cela comptait plus que le reste

**Le banc est l'instrument qui décide de ce que le projet embarque.** L'ADR 0006
a gardé une règle et en a rejeté deux sur des écarts de trois à six points. Un
banc de trente-trois batailles bouge de trois points dès qu'une seule bascule —
et c'est exactement ce que le hachage faisait basculer.

L'ADR 0010 a mesuré à la règle de contagion un coût de neuf points, entièrement
porté par `balanced_clash` : le seul scénario dont on sait aujourd'hui qu'il
basculait tout seul. **Ce chiffre ne peut plus être invoqué.** Le rejet de la
règle tient toujours, mais par les mesures faites sur les batailles réelles, pas
par le banc.

Il faut le dire dans l'autre sens aussi, parce que c'est le plus gênant : la
supervision était réputée « sans régression » à 91 %. Sur l'instrument corrigé
elle mesure **91 % → 88 %**, avec `balanced_clash` à 100 % → 67 %. Le verdict
rassurant était le bruit, pas le résultat.

> Le compte des déclenchements d'une règle, lui, n'a jamais souffert du défaut :
> zéro intervention reste zéro quelle que soit la graine. C'est la mesure qui a
> le mieux tenu, et c'est elle qui a rejeté `plus_aucune_reserve`.

## Le garde-fou

`tests/integration/test_bench_determinism.py` relance l'interpréteur avec deux
graines de hachage et compare les issues. Un test tournant dans un seul processus
**ne peut pas** voir ce défaut : le hachage y est fixé une fois pour toutes. Il
fallait un sous-processus, et deux valeurs choisies pour diverger réellement —
vérifié en réintroduisant le défaut, qui fait bien échouer le test.

## Ce qu'il faut en retenir

Un banc qui n'est pas reproductible n'est pas un banc. Avant de rejouer une
comparaison à quelques points, la question n'est pas « qu'est-ce que la mesure
dit », mais **« la mesure dit-elle deux fois la même chose »**.

Les écarts déjà publiés dans les ADR antérieures à celle-ci n'ont pas été
rejoués. Ils doivent être lus comme indicatifs, et repris avant de servir de
nouveau à trancher.

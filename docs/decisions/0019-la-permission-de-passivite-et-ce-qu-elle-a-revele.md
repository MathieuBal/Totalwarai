# 0019 — La permission de passivité, et les quatre instruments qu'elle a démasqués

**Statut :** retenu — banc inchangé à 82 % (12 graines : 79 %), aucune
régression ; la permission ne change aucune issue, et c'est le résultat —
18/08/2026

## Ce qui était proposé

En posture `DEFEND`, l'assaut de secteur est interdit. Cette interdiction est
justifiée et mesurée : la lever partout faisait tomber `balanced_clash` de 100 %
à 0 %. Mais les deux scénarios sans victoire y restaient enfermés —
`skirmish_standoff` répétait « tenir la position » 320 fois en deux minutes,
sans une seule attaque.

Le plan proposait donc un détecteur qui **ne commande rien** et se contente de
lever l'interdiction quand l'adversaire est prouvé passif, l'assaut gardant tous
ses garde-fous. Trois issues étaient annoncées, avec l'engagement de dire
laquelle arriverait :

> 2. la permission est accordée et **aucun assaut ne passe les garde-fous** — le
> scénario reste un nul, mais on sait alors que le blocage est dans les
> conditions de l'assaut, pas dans l'interdiction.
>
> **L'issue 2 est la plus probable.**

**C'est l'issue 2.** Permission accordée sur **56 des 60 plans** de
`skirmish_standoff`, et **zéro assaut**. Le scénario reste un nul.

Ces deux chiffres sont désormais publiés par le banc lui-même —
`assault_permissions` à côté de `sector_assaults` — parce que « la permission
n'est jamais accordée » et « elle l'est, et aucun assaut ne passe les garde-fous »
sont deux diagnostics opposés qui produisent exactement le même banc : celui où
il ne se passe rien.

## Ce que le plan affirmait à tort

Le plan garantissait la sûreté de `balanced_clash` ainsi :

> Sur `balanced_clash`, l'ennemi vient à nous et inflige des pertes — la
> passivité n'est jamais constatée, la permission n'est jamais accordée, et rien
> ne change.

**La moitié en est fausse, et il a fallu construire un compteur pour savoir
laquelle.** La passivité *est* constatée sur `balanced_clash` — 40 plans sur 61.
Mais la permission, c'est-à-dire l'interdiction effectivement levée, est accordée
**zéro fois**, parce que ces moments-là ne sont pas en posture `DEFEND`, où il
n'y a rien à lever.

C'est précisément la distinction que `assault_permissions` existe désormais pour
trancher : « la passivité est constatée » et « l'assaut redevient examinable »
sont deux choses, et je les avais d'abord confondues dans les deux sens — le plan
niait la première, ma première relecture de la mesure niait la seconde.

Pourquoi la passivité est constatée là-bas, et pourquoi c'est bénin :

```
t=  32.5 force_ennemie= 5.55  distance= 35.6  saigne=True   passif_depuis= 11.0s
t=  98.0 force_ennemie= 1.76  distance=188.1  saigne=True   passif_depuis= 10.0s
t= 188.5 force_ennemie= 1.46  distance=212.7  saigne=False  passif_depuis= 20.5s
```

Pendant toute la phase décisive, l'ennemi saigne à chaque relevé et le compteur
ne dépasse jamais 11 secondes — très loin des 40 requises. La passivité n'est
constatée qu'après t≈178 s, quand l'adversaire est réduit à 1,46 et **s'enfuit** ;
l'agent est alors en posture offensive, et aucune interdiction n'est levée.

Le scénario est donc bien protégé, et par **deux** mécanismes indépendants là où
le plan n'en invoquait qu'un, à tort. Un argument de sûreté approximatif vaut
moins qu'un argument mesuré : celui du plan aurait couvert un changement qui,
lui, aurait mordu.

## Quatre instruments qui mentaient

Chercher pourquoi aucun assaut ne partait a démasqué quatre défauts. Aucun n'
était dans la doctrine ; tous étaient dans ce qui sert à la mesurer ou à la
nourrir.

### 1. Un seuil dont le sens dépendait de la cadence d'échantillonnage

`MobilityTracker` déduit la vitesse du déplacement observé, en ignorant ce qui
passe sous `STILL_DISTANCE = 3.0` m. Relevés bruts sur `skirmish_standoff` :

```
a_inf2    parcouru=  7.98 m en 10.0 s ->  0.80 m/s   RETENU
a_cav1    parcouru= 15.96 m en 10.0 s ->  1.60 m/s   RETENU
```

Un tassement de formation au déploiement, enregistré comme vitesse de marche —
et **définitivement**, puisque ces unités tiennent ensuite leur position et ne
fournissent plus jamais de relevé.

La cause : **une distance ne veut rien dire sans son intervalle.** Le seuil a
été calibré pour le pas de simulation — 3 m en 0,5 s valent 6 m/s, une vraie
marche — puis employé dans un module alimenté **une fois par plan, toutes les
dix secondes**, où les mêmes 3 m valent 0,3 m/s.

Conséquence mesurée : ETA de 240 à 270 s vers un secteur distant de 200 m, tout
le monde écarté par `ASSAULT_DEADLINE`, **1 à 2 unités « atteignables » sur 6**
et aucun secteur jamais tenable. La primitive de concentration locale ne pouvait
pas s'exercer du tout en posture défensive.

`WALK_SPEED = 2.0` exprime le garde-fou en **vitesse**, ce qui ne dépend pas de
l'intervalle. La valeur est encadrée des deux côtés : au-dessus de tout
tassement mesuré (0,80 et 1,60), en dessous de l'a priori du rôle le plus lent
qui puisse porter un assaut (infanterie, 4,0). Après correction : **5 à 6 unités
atteignables sur 6**, et un secteur tenable à 4,92.

### 2. Ce que l'ADR 0018 affirmait, et qui n'a jamais été vrai

L'ADR 0018 justifiait ce seuil ainsi :

> le seuil d'immobilité vaut `STILL_DISTANCE = 3.0`, la même valeur que
> `learning.observation` — décider sur un seuil et mesurer sur un autre ne
> validerait rien.

**L'accord annoncé n'a jamais existé en fait.** `learning.observation` travaille
sur le relevé à 2 Hz, `MobilityTracker` sur des plans à 10 s. Deux constantes de
même valeur et de sens incomparables : partager le nombre donnait l'apparence de
la cohérence sans la chose.

### 3. Un rapprochement mesuré contre le meilleur jamais atteint

`PassivityWatch` gardait le **minimum** de distance jamais observé, pour qu'une
oscillation ne remette pas le compteur à zéro. Mais comparer au plus proche
*jamais* atteint rend tout rapprochement ultérieur indétectable dès qu'un des
deux camps a reculé une fois : sur `balanced_clash`, la distance passe de 35,6 m
à 38,4 puis à 200, et `approche` reste faux pour le reste de la bataille. Un
ennemi qui se serait ressaisi aurait été tenu pour inerte.

Le code contredisait sa propre docstring — « ce que l'adversaire a fait **depuis
le dernier plan** ». Il compare désormais au relevé précédent ; l'oscillation
reste couverte par `APPROACH_EPSILON`, dont c'est le rôle. Défaut latent : la
correction ne change aucun compte sur le banc.

### 4. Le tableau du banc ne distinguait pas un nul d'une défaite

Les issues étaient abrégées par leur première lettre. `defeat` et `draw` donnent
**« d » toutes les deux**, sur la seule colonne qui dit qui a gagné.

Ce n'est pas cosmétique : au cours de cette session, `skirmish_standoff` est
passé du nul à la défaite sur ses trois graines **sans que la colonne bouge d'un
caractère**, et il a fallu un script séparé pour s'en apercevoir. Une ligne
« 2d, 1d » ne se lit même pas comme une anomalie, alors qu'elle annonce deux
défaites et un nul.

Corrigé — et le premier banc honnête a livré aussitôt une seconde surprise :

| scénario | 3 graines | **12 graines** |
| --- | --- | --- |
| `balanced_clash` | 3v — 100 % | **1 défaite, 3 nuls, 8 victoires — 67 %** |
| `outnumbered` | 2 défaites, 1 nul | 9 défaites, 3 nuls |

`balanced_clash` n'est pas le scénario solidement gagné que le banc court
laissait croire.

## L'expérience qui a déplacé le blocage, et qu'il a fallu retirer

Une fois les secteurs redevenus tenables, `commit()` refusait encore l'assaut
**aux 56 plans**. La cause est nette :

```
t=40s  secteur 2  coût=1.00  besoin=1.50  fenêtre=15.0s
    ETA  44.1s  a_cav1     force 1.00   <- seule retenue, 1,00 < 1,50
    ETA  62.4s  a_spear1   force 1.00   <- écartée : 62,4 - 44,1 > 15 s
    ETA  63.8s  a_inf3     force 1.00
    ETA  67.9s  a_inf2     force 1.00
```

`ASSAULT_WINDOW` était ancrée sur la **première** arrivée : la cavalerie
devançait l'infanterie de dix-huit secondes, et son avance opposait son veto aux
trois unités qui, elles, arrivaient à cinq secondes les unes des autres. Or la
règle dit que l'assaut doit arriver groupé — **elle ne dit pas qu'il doit
comprendre l'unité la plus rapide.**

Une fenêtre glissante, essayant chaque départ possible, garantit exactement la
même dispersion et lève le veto. Mesure : **56 assauts** là où il n'y en avait
aucun. Et le banc :

| | avant | fenêtre glissante |
| --- | --- | --- |
| `skirmish_standoff` | 3 nuls | **3 défaites** |
| forces restantes | 100 % | **74 %** |
| forces ennemies restantes | 100 % | 98 % |

**Nous perdons un quart de notre armée pour 2 % de la leur.** Le changement est
retiré.

Il n'était pourtant pas faux, et la mesure dit exactement où est le vrai défaut :
sur `skirmish_standoff`, **le seul secteur qui atteigne jamais 1,5 est un ennemi
isolé**, à 4,92 — les deux autres plafonnent à 1,17 et 1,23. `best()` n'a aucun
choix à faire, et l'enfoncer coûte notre ligne pour un huitième de leur armée.
C'est le défaut que l'ADR 0018 avait déjà noté sur `outnumbered` — « l'agent y a
laissé toute sa ligne de mêlée » — et le veto de la fenêtre le masquait.

**Le prochain chantier est donc le choix du secteur, pas la composition.** Un
rapport local élevé obtenu sur un détachement isolé n'est pas une opportunité :
c'est le secteur le moins cher à prendre et le moins utile à tenir.

## Ce que cela laisse

Banc inchangé : 82 % sur 3 graines, 79 % sur 12, forces restantes 80 %, aucune
régression. **La permission ne change aucune issue** — publiée telle quelle,
comme le plan s'y engageait, plutôt que rattrapée par un seuil.

Ce qu'elle a rapporté n'est pas une victoire mais un déplacement du problème,
vérifié à chaque étape : l'interdiction en `DEFEND` n'était pas ce qui bloquait ;
la vitesse observée était fausse ; la fenêtre d'arrivée était mal appliquée ; et
ce qui reste est le choix du secteur. Gate A n'est pas franchie, et le verdict le
dit à chaque exécution.

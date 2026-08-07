# 0010 — Concentrer plutôt que décrocher : deux règles rejetées, un défaut trouvé

**Statut :** deux règles écrites puis rejetées, une correction retenue — 07/08/2026

## Le problème posé

Après une série de défaites en jeu, le constat était clair : l'agent « joue
comme un débutant ». Deux règles de supervision ont été écrites pour y répondre,
et **les deux ont été rejetées**. Le travail utile a été la mesure qui les a
rejetées : elle a désigné le vrai défaut.

## Ce qui a été écrit puis retiré

### `contagion_de_deroute`

L'idée : retirer une unité entourée d'alliés qui rompent, pour la sortir de la
cascade de moral. Le mécanisme est réel — sur la première bataille proprement
enregistrée, douze unités sur douze ont rompu, dont dix au-dessus de 40 % de
santé.

Le banc supervisé, onze scénarios × trois graines :

| Jeu de règles | Victoires | Forces | Seigneur |
| --- | --- | --- | --- |
| aucune règle | 91 % | 71 % | 76 % |
| les trois anciennes | 91 % | 70 % | 88 % |
| + contagion | **82 %** | 70 % | 88 % |
| + réserve | 91 % | 70 % | 88 % |
| les cinq | **82 %** | 70 % | 88 % |

Neuf points perdus, imputables à la seule contagion. Un unique scénario bascule,
`balanced_clash`, sur les trois graines : un affrontement à parité (34,8 % de
forces restantes contre 35,7 %) où la règle retire trois unités sur huit au
moment décisif.

> **⚠ Ce tableau a été mesuré sur un instrument faussé, et il ne doit pas être
> rejoué tel quel.** La poursuite de ces chiffres a fini par découvrir que le
> banc dépendait de `PYTHONHASHSEED` : `balanced_clash` rendait « victoire » ou
> « nul » à graine identique selon le processus. Or c'est précisément
> `balanced_clash` qui portait tout l'écart. **Les neuf points peuvent n'être
> que ce bruit** — trois batailles sur trente-trois font trois points, et une
> seule bascule en fait autant.
>
> Le défaut est corrigé (`simulation/environment.py`, tri de `engaged_with`) et
> gardé par `tests/integration/test_bench_determinism.py`. Le rejet de la règle,
> lui, ne repose pas sur ce tableau : il repose sur les deux mesures faites sur
> les batailles réelles, ci-dessous, qui ne dépendent d'aucun banc.

Restreindre la règle aux unités **non engagées** n'a rien changé — les unités
qu'elle retirait étaient déjà libres. Restait à régler le seuil. Le balayage :

| rayon | voisins | victoires |
| --- | --- | --- |
| 50 m | 2 | 82 % |
| **50 m** | **3** | **91 %** |
| 75 m | 3 | 82 % |
| 75 m | 4 | 91 % |
| 100 m | 2 | 82 % |

Un réglage neutre existe donc. Mais il est neutre **parce qu'il ne se déclenche
jamais** : à 50 m / 3 voisins, zéro intervention sur les trente-trois batailles
du banc. Ce comptage-là, contrairement aux taux de victoire, ne souffre pas du
défaut d'instrument : zéro déclenchement reste zéro quelle que soit la graine de
hachage.

La question devenait : ce seuil verrait-il la cascade réelle ? Mesuré sur les
deux batailles jouées, en comptant pour chaque unité qui bascule les alliés déjà
en fuite autour d'elle :

| réglage | déroutes annoncées |
| --- | --- |
| 50 m / 3 voisins | 3/24 |
| 75 m / 3 voisins | 6/24 |
| 100 m / 2 voisins | **17/24** |

**Le seuil qui voit la cascade réelle est exactement celui que le banc
sanctionne, et le seuil que le banc accepte est aveugle.** Il n'existe pas de
réglage qui soit les deux.

Pire, le calendrier condamne l'idée même. Sur la première bataille, les déroutes
tombent à 97 s, puis 383, 402, 402, 418, 450, 458, 461, 472, 502 s. La règle ne
peut se déclencher qu'à partir de la troisième — quand quatre unités sont déjà
parties. **C'est une réaction à l'effondrement, pas une prévention.** Retirer une
cinquième unité de la ligne à cet instant accélère la chute au lieu de l'arrêter.

Règle rejetée, comme `tireur_isole` l'avait été à l'ADR 0006, et pour la même
raison : elle coûte des batailles mesurées contre un gain qui ne l'est pas.

### `plus_aucune_reserve`

L'idée : garder une unité en arrière quand toute l'armée est au contact. Sa
justification affirmée était « 76 à 84 % de l'armée engagée au moment de
l'effondrement ».

**Ce chiffre est faux, et c'est la mesure qui l'a montré.** La part de l'armée
*simultanément* au contact culmine à 67 % et 50 % sur les deux batailles
réelles, médiane 0 % et 18 %. Le seuil de 70 % n'est jamais atteint : la règle
ne s'est déclenchée ni au banc (zéro intervention sur trente-trois batailles)
ni sur aucun des 1 942 états enregistrés en jeu.

Une règle dont la prémisse est démentie et qui ne se déclenche nulle part n'est
pas neutre, elle est morte. Rejetée.

> La leçon vaut d'être écrite : le banc annonçait « neutre » pour les deux
> réglages survivants, et « neutre » voulait dire « muet » dans les deux cas.
> **Un jeu de règles ne se juge pas sur son score sans compter ses
> déclenchements.**

## Le défaut, lui, a été trouvé

La médiane d'engagement à 0 % posait une question : que fait l'armée pendant que
rien n'est au contact, et pourquoi cède-t-elle en deux minutes ?

Mesure du rapport de forces **local** — pour chaque unité alliée en mêlée, les
ennemis contre les alliés à moins de quarante mètres :

| | bataille 1 | bataille 2 |
| --- | --- | --- |
| mêlées relevées | 1 477 | 838 |
| rapport local médian | 1,50 | 1,67 |
| mêlées livrées en infériorité locale | **65 %** | **58 %** |
| pic du rapport local | **2,00** (7ᵉ min) | **3,00** (8ᵉ min) |

Le rapport global était de 1,2 contre nous. Localement nous combattions à 1,5
puis 2 contre 1 — et le pic tombe entre la 383ᵉ et la 502ᵉ seconde, c'est-à-dire
pendant la cascade. **La cascade de moral n'est pas la cause, c'est le symptôme :
nous étions battus en détail.**

> Ces chiffres sont ceux que rend `learning/concentration.py`, qui exclut les
> fuyards des deux camps. Le premier relevé, qui les comptait, donnait 69 % et
> 54 % : l'écart ne change pas le verdict, et la version qui exclut les fuyards
> est la bonne — une unité qui rompt ne menace plus et ne soutient plus.

### La cause était dans notre code

`Planner.score_target` retirait 0,20 par unité déjà envoyée sur une cible. Cette
pénalité de saturation existe pour éviter que douze unités s'empilent sur une
seule — mais c'est une **pression de dispersion permanente**, qui étale notre
ligne pendant que l'adversaire concentre la sienne.

## Ce qui est retenu

`Planner.local_balance` note, pour chaque cible de mêlée, le rapport de forces
que l'engagement nous laissera **une fois arrivés** — l'attaquant compté dans
les nôtres, les fuyards comptés dans aucun camp. Le terme vaut zéro à parité
locale, et la parité suffit : il s'agit de ne pas ouvrir une mêlée perdue
d'avance, pas d'empiler l'armée sur une unité.

Le banc autonome, cinq poids, onze scénarios × trois graines — **rejoué après la
correction de l'instrument**, et reproductible d'un processus à l'autre :

| poids | victoires | forces | seigneur |
| --- | --- | --- | --- |
| 0,0 | 82 % | 78 % | 100 % |
| 0,4 | 82 % | 78 % | 100 % |
| 0,8 | 82 % | **79 %** | 100 % |
| 1,2 | 82 % | 78 % | 100 % |
| 1,6 | 82 % | 79 % | 100 % |

### Pourquoi retenir un gain d'un point

**Parce que le banc ne peut pas montrer davantage : il n'a pas le défaut.** La
doublure combat déjà à un rapport local médian de 1,00, avec 32 % de mêlées en
infériorité — contre 1,50 et 65 % en jeu. Le terme de concentration fait passer
le banc de 32 % à 30 % parce qu'il n'y avait presque rien à corriger.

La différence avec la contagion est là, et elle est décisive :

* la contagion coûtait **neuf points mesurés** contre un bénéfice invérifiable ;
* la concentration coûte **zéro point mesuré** contre un défaut mesuré en jeu.

C'est le seul cas où l'on peut expédier sans preuve de bénéfice : quand la preuve
d'innocuité, elle, est faite. Le poids est fixé à 0,80 — le plus petit qui
produise l'effet, les valeurs supérieures n'apportant rien.

> **Ce comportement n'est pas démontré en jeu.** Le banc dit qu'il ne nuit pas ;
> il ne dit pas qu'il sert. La mesure qui tranchera est la reprise du rapport
> local sur la prochaine bataille jouée : il doit descendre sous 1,50, et la part
> de mêlées en infériorité sous 65 %.

## Ce que cela laisse ouvert

Le moral reste invisible et la cascade reste réelle. La conclusion de cette ADR
n'est pas qu'elle ne compte pas, mais qu'**on ne l'arrête pas en décrochant des
unités une fois qu'elle a commencé** — il faut ne pas se faire battre en détail
avant. La prédiction des déroutes à partir des signaux observables, seul endroit
du projet où les données abondent, reste la piste ouverte.

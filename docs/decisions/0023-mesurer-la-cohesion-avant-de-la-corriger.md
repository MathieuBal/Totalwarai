# 0023 — Mesurer la cohésion avant de la corriger

**Statut :** retenu — baseline figée et rejouable par `totalwar-ai cohesion` —
19/08/2026

## Le défaut, en chiffres

Bataille du 18/08 22h20, premiers contacts :

```
1010   192,6 s      trois unités rapides, parties seules
1011   206,6 s
1012   241,6 s
─────────────────  235,5 secondes de vide
1003   477,1 s      le premier fantassin de la ligne
1004   623,1 s
1002   687,6 s
```

| mesure | valeur |
| --- | --- |
| premier contact | **192,6 s** (1010) |
| cohorte à +30 s | 2/12 — 17 % |
| **cohorte à +60 s** | **3/12 — 25 %** |
| cohorte à +120 s | 3/12 — 25 % |
| vagues de contact | 5 |
| **première vague** | **3/12 — 25 %** |
| **plus grand vide** | **235,5 s** (1012 → 1003) |
| armée commandée à +30 s | 25 % |
| armée mise en mouvement à +30 s | 25 % |

C'est la traduction chiffrée de « se faire manger au compte-gouttes ».

```
totalwar-ai cohesion --battle <corpus>
```

**La baseline est rejouable.** Ces chiffres ont d'abord été produits par un
script jetable, ce qui les rendait inutilisables comme juge : personne n'aurait
pu refaire la mesure à l'identique après un changement de doctrine.

## La métrique principale n'est pas celle qu'on croyait

`isolated_contact` **ne porte pas** le phénomène observé. Avec le seuil à une
voisine, `1010/1011/1012` ne sont pas isolés — ils sont trois ensemble, et c'est
délibéré : un flanc à trois est une manœuvre légitime.

Ce qui porte le défaut, ce sont `contact_cohort`, `largest_contact_gap` et les
**vagues de contact** — des contacts successifs regroupés tant que leur intervalle
reste sous la fenêtre de soutien :

```
vague 1   1010 192,6   1011 206,6   1012 241,6
          ────────── 235,5 s ──────────
vague 2   1003 477,1
```

Cette formulation décrit le phénomène **sans prétendre que la première vague était
illégitime**. Elle dit seulement : trois unités entrent au combat, puis personne
ne les rejoint pendant presque quatre minutes.

### Ce sont des vagues de **mêlée**

Le contact se lit sur `in_melee`. Une manœuvre parfaitement coordonnée peut
n'avoir que deux régiments en mêlée — les autres fixant, tirant à cent vingt
mètres, ou attendant un flanc. Une lecture naïve y verrait `2/12` et conclurait à
une mauvaise cohésion alors que les douze tiendraient leur rôle. D'où le mot
`contact` dans chaque nom, et l'interdiction de les convertir mécaniquement en
`participant_wave_share` le jour où `MANOEUVRE.participants` existera : la
participation devra alors être fonction du rôle, et le juge sera
`participant_ready_share`.

## Quatre défauts de la mesure, trouvés par la mesure elle-même

**Les ordres antérieurs à `Deployed` gonflaient tout.** La première version
annonçait « armée ordonnée à 100 % » : les douze unités avaient bien reçu un
ordre à 3,1 s — avant le début de `Deployed` à 7,6 s, donc sans qu'aucune ne
bouge. Le moteur les acquitte sans les exécuter. Une fois écartés : **25 %**, ce
qui correspond aux trois unités réellement parties.

**La médiane noyait le vide.** Le délai de soutien médian vaut 727 s sur cette
bataille — un chiffre vrai et inutilisable, qui mélange le renfort attendu et des
arrivées appartenant déjà à une autre phase. `largest_contact_gap` désigne
l'endroit exact : 235,5 s.

**Le dénominateur pouvait perdre des unités.** Le lecteur construisait l'armée à
partir des unités *ayant reçu un ordre*, tout en affirmant en commentaire
l'inverse. Une doctrine future qui aurait complètement oublié deux régiments les
aurait fait disparaître de l'armée étudiée, et **ses ratios se seraient
améliorés**. Le `roster` du `BattleRecorder` porte `side`, donc la population
canonique : les alliés sont l'armée, les ordres et les contacts ne font que
renseigner leurs instants. Le même lecteur enregistrait par ailleurs `in_melee`
pour tous les identifiants de l'inventaire, ennemis compris.

**Le roster est canonique, mais temporel.** Il est republié quand des unités
arrivent en cours de bataille — un second corpus contient un treizième allié
apparu à 648 s alors que le premier contact avait lieu à 86 s. Le compter aurait
donné `3/13` pour une unité qui ne pouvait pas participer : d'où `first_seen_at`,
et un dénominateur restreint aux alliés **déjà présents** au premier contact.

**`HOLD` n'est pas une mise en mouvement.** Une doctrine qui enverrait douze
`HOLD` afficherait « armée ordonnée à 100 % » sans que personne ne bouge. D'où
deux mesures séparées — `commanded_army_share` (déplacements, attaques et arrêts)
et `active_order_share` (déplacements et attaques seulement) — dont seule la
seconde peut prétendre décrire une mobilisation. Sur cette bataille les deux
valent 25 %, les trois unités concernées ayant reçu de vrais `MOVE`.

## Le banc ne reproduit pas ce défaut — mais il en a un autre

`totalwar-ai cohesion`, onze scénarios × trois graines :

| | banc | bataille réelle |
| --- | ---: | ---: |
| plus grand vide, maximum | **17,0 s** | **235,5 s** |
| plus grand vide, médiane | 8,0 s | — |

Dix-sept secondes contre deux cent trente-cinq. Comme pour LIVE-001, **le banc ne
peut pas arbitrer ce chantier** : ses engagements sont serrés, et un correctif
mesuré sur lui seul ne montrerait presque rien à améliorer.

**Cela ne veut pas dire que le banc n'a aucun problème de cohésion.** Une absence
de contact n'est pas une bonne cohésion de contact : elle est **non définie**, et
c'est un autre défaut à compter à part. Trois états, donc, et pas deux :

| état | scénarios | vide mesurable |
| --- | --- | --- |
| mêlée comparable | 21 batailles | oui |
| **contact unique** | `numerical_superiority` (3 graines) | **non** |
| sans contact | `artillery_assault`, `rout_pursuit`, `skirmish_standoff` | **non** |

Le cas du milieu est le piège. `largest_contact_gap` exige deux entrées en mêlée :
une bataille où **une seule unité sur douze** engage pendant que onze regardent
n'a pas de vide mesurable — exactement comme une bataille sans aucun contact. Une
agrégation qui se serait contentée de moyenner les vides définis aurait donc fait
sortir le pire cas par la même porte que l'absence de défaut. Un vide absent ne
devient jamais zéro : zéro voudrait dire « aucun délai ».

`numerical_superiority` gagne ses trois batailles, et n'engage pourtant qu'une
unité. Ce n'est pas dans le périmètre de cet ADR, mais la mesure l'a vu dès sa
première exécution, et il n'aurait pas été visible autrement.

## Un correctif appliqué à la mauvaise classe

En marge de la mesure, la relecture a trouvé un défaut que je croyais fermé.

L'ADR précédent annonçait que la phase `unknown` n'était plus jouable et que les
treize ordres acquittés sans effet du 18/08 étaient écartés. Le dépôt portait en
réalité **deux** propriétés `orders_take_effect` :

```text
ProbeUnitState.orders_take_effect     durcie
ProbeBattleState.orders_take_effect   acceptait encore « unknown »
```

`LiveSession._decide()` reçoit un `ProbeBattleState`, et la boucle d'attente de
`probe --play` aussi. **Le durcissement n'avait jamais atteint le pilotage.** Le
test qui le validait interrogeait `ProbeUnitState` — une classe que le pilotage
n'emprunte pas : il était vert et ne prouvait rien.

La règle est désormais une fonction unique à laquelle les deux classes délèguent,
et le test part de la sonde pour aller jusqu'aux ordres arrivés dans le jeu :
aucune classe intermédiaire ne peut le satisfaire à la place de la bonne.

Deux phases restent hors de portée de ce test. La chaîne vide ne peut plus venir
que d'une régression de protocole, la révision 16 publiant toujours le champ ;
elle est couverte au niveau du modèle. `VictoryCountdown` n'est pas dans les
`PHASES` de la sonde — le jeu l'annonce dans son propre journal, mais la sonde
reste sur `Deployed`, et pendant le décompte un ordre prend effectivement encore
effet.

## Ce que la mesure ne dit pas

**Aucun seuil n'est imposé.** « Toute l'armée doit avancer » serait un mauvais
critère : une diversion à deux unités, un flanc à trois, une réserve délibérée,
une ligne qui fixe pendant qu'un groupe frappe sont toutes des manœuvres
légitimes. Le contrat visé est plus étroit :

> une unité ne doit pas déclencher un engagement qu'elle est censée mener avec du
> soutien, si ce soutien n'est pas en situation de participer.

`isolated_contact` n'en est qu'une **approximation temporelle** — et une mesure
secondaire, pas celle qui porte le défaut observé. Le seuil est
volontairement permissif : n'est isolée que l'unité dont **aucune** voisine ne
combat dans la fenêtre de soutien. Il valait d'abord deux voisines, ce qui
condamnait une diversion délibérée — sans notion de manœuvre, rien ne distingue
une paire voulue d'une paire accidentelle.

Le jour où le planificateur portera une manœuvre avec ses participants, ce calcul
devra porter sur eux et non sur toute l'armée.

## Ce qui suit

Le manque est architectural. Le planificateur sait produire une décision de
front, une de cavalerie, une de tir, une de réserve, une de commandement —
successivement et indépendamment. Il lui manque une couche **au-dessus** :

```
MANOEUVRE
  ├── objectif            ├── participants et rôles
  ├── point de rassemblement
  ├── condition de déclenchement
  ├── condition d'abandon └── phase courante
```

Phases candidates : `ASSEMBLE → FIX/SUPPORT → CONTACT → EXPLOIT`. **Elles ne
seront pas implémentées avant que cette baseline ait servi de point de
comparaison**, et le banc ne pourra pas en être le seul juge.

Ni `duplicate_suppression` ni la dispersion des dégâts ne font l'objet d'un
chantier séparé : les deux se remesurent après, parce que les deux peuvent en
être des conséquences.

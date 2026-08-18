# 0023 — Mesurer la cohésion avant de la corriger

**Statut :** retenu — baseline publiée, le banc ne reproduit pas le défaut —
18/08/2026

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
| **plus grand vide** | **235,5 s** (1012 → 1003) |
| armée ordonnée à +30 s | 25 % |
| engagements isolés | **3** (1003, 1004, 1002) |

C'est la traduction chiffrée de « se faire manger au compte-gouttes ».

## Deux défauts de la mesure, trouvés par la mesure elle-même

**Les ordres antérieurs à `Deployed` gonflaient tout.** La première version
annonçait « armée ordonnée à 100 % » : les douze unités avaient bien reçu un
ordre à 3,1 s — avant le début de `Deployed` à 7,6 s, donc sans qu'aucune ne
bouge. Le moteur les acquitte sans les exécuter. Une fois écartés : **25 %**, ce
qui correspond aux trois unités réellement parties.

**La médiane noyait le vide.** Le délai de soutien médian vaut 727 s sur cette
bataille — un chiffre vrai et inutilisable, qui mélange le renfort attendu et des
arrivées appartenant déjà à une autre phase. `largest_contact_gap` désigne
l'endroit exact : 235,5 s.

## Le banc ne reproduit pas ce défaut non plus

Baseline de cohésion, onze scénarios, graine 11 :

| | banc | bataille réelle |
| --- | ---: | ---: |
| plus grand vide, maximum | **15,5 s** | **235,5 s** |
| engagements isolés, total | **1** | **3** |

Quinze secondes contre deux cent trente-cinq. Comme pour LIVE-001, **le banc ne
peut pas arbitrer ce chantier** : ses engagements sont serrés, et un correctif
mesuré sur lui seul ne montrerait presque rien à améliorer.

Trois scénarios n'engagent jamais le combat — `artillery_assault`,
`rout_pursuit`, `skirmish_standoff` — ce qui est cohérent avec ce que l'ADR 0020
avait déjà mesuré sur les secteurs.

## Ce que la mesure ne dit pas

**Aucun seuil n'est imposé.** « Toute l'armée doit avancer » serait un mauvais
critère : une diversion à deux unités, un flanc à trois, une réserve délibérée,
une ligne qui fixe pendant qu'un groupe frappe sont toutes des manœuvres
légitimes. Le contrat visé est plus étroit :

> une unité ne doit pas déclencher un engagement qu'elle est censée mener avec du
> soutien, si ce soutien n'est pas en situation de participer.

`isolated_contact` n'en est qu'une **approximation temporelle**, et le seuil est
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

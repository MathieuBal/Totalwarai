# 0021 — Nommer l'étage où la commande disparaît

**Statut :** retenu — le banc ne reproduit pas le défaut, et c'est le résultat ;
banc inchangé 82 % / 80 %, aucune régression — 18/08/2026

## Le fait

En bataille réelle, l'agent est resté **364 secondes sans émettre une seule
commande** pendant que son armée passait de 12 à 9 unités, l'ennemi n'en perdant
aucune. Première attaque à 852,1 s, après trois unités perdues contre zéro.

## Ce que la mesure a écarté

### Le banc ne reproduit rien de tel

Plus longue fenêtre sans commande, par scénario, `order_ttl` valant 6 s :

| scénario | plus long silence |
| --- | --- |
| `outnumbered` | 11,0 s |
| `balanced_clash` | 10,5 s |
| tous les autres | 2,0 à 6,0 s |

**Onze secondes contre trois cent soixante-quatre.** Aucun scénario n'approche
l'ordre de grandeur. Ce n'est donc pas un défaut que le banc pouvait révéler, et
ajouter des scénarios n'y aurait rien changé.

### La suppression de doublons domine les comptes, et c'est nominal

Comptés par tour, les étages muets donnent `duplicate_suppression` partout — 796
tours sur 957 pour `balanced_clash`, qui est pourtant **gagné**. Le compte par
tour désignait donc un coupable dans les scénarios où tout va bien.

> Ce qui distingue le fonctionnement normal de la paralysie n'est pas l'étage,
> c'est la **durée**. Re-proposer un ordre encore valide est correct ; le
> re-proposer sans que rien ne parte pendant six minutes ne l'est pas.

C'est la même leçon que pour la cadence : un mécanisme nominal qui domine les
comptes n'est pas une cause.

### Le Lua publiait — et c'est tout ce que ce journal prouve

Pendant les 364 secondes, le journal du jeu montre :

* les états publiés **à 2 Hz sans interruption** (occurrences 600 → 620 → 640) ;
* les séquences de commande **consécutives** : 192 à 312,1 s, puis 193 à 676,1 s.

> **Correction.** Une première version de cette ADR concluait « Python a donc
> reçu environ sept cents états ». C'est une affirmation que ce journal ne porte
> pas : il prouve que **Lua a continué à publier les états**, pas que Python les
> consommait en temps réel. Une boucle suspendue puis rattrapant son arriéré
> produirait exactement le même journal côté jeu — et exactement le même silence
> de commandes.

La chaîne à instrumenter commence donc **un étage plus tôt** que je ne l'avais
écrit, par `PYTHON_LOOP`, et il faut deux horloges pour trancher : en cas de
blocage, les `game_time_ms` paraîtront continus tandis que le `wall_clock`
révélera le trou.

## Ce que la mesure a trouvé

Deux étages existent **uniquement dans le pont**, et le banc ne les traverse
jamais parce qu'il appelle l'agent directement :

```
planner -> confidence -> safety -> duplicates -> throttle
        -> traduction -> filtre de micro-deplacements -> Lua
```

Et `_drop_micro_moves` documente, dans sa propre docstring, **la même paralysie
déjà constatée** :

> Bataille `a1274d62` : douze déplacements à t=3 s, puis **cent quatre-vingt-dix
> secondes sans un ordre**, jusqu'à ce que l'opérateur déplace une unité à la
> souris — ce qui décalait sa position et faisait enfin franchir le seuil à la
> destination recalculée.

Même signature qu'aujourd'hui, y compris l'intervention à la souris pour
débloquer. Cent quatre-vingt-dix secondes alors, trois cent soixante-quatre
maintenant.

**Ce n'est pas encore une conclusion.** Un correctif avait été écrit pour ce
défaut, et le journal ne dit pas s'il a échoué, s'il a été contourné, ou si la
cause est ailleurs. Ce qu'on sait avec certitude est que **le banc ne peut pas
répondre**, et qu'il fallait donc instrumenter le pont.

## Ce qui est instrumenté

`AgentTurn` porte `decision_due` et les comptes par étage ; `LiveStep` les reprend
et ajoute les deux étages du pont. Un tour muet nomme désormais son étage :

```
t= 400.0s 9 allies / 10 ennemis — NO_COMMAND stage=micro_move
          [proposed=5, duplicates=3, emitted_by_agent=2, micro_dropped=2]
```

Trois précautions, chacune tirée d'un défaut de cette session :

* **`decision_due`** — la cadence nominale n'est jamais un étage. Sans elle,
  « cadence » aurait dominé tous les comptes en désignant le fonctionnement
  normal ;
* **l'étage prime sur le détail.** Un tour muet affichait « 2 déjà en cours » et
  rien d'autre : un motif de silence déguisé en compte rendu d'activité. « Déjà
  en cours », « refusée par la sécurité » et « action perdue » ne sont pas des
  commandes émises, ce sont les raisons pour lesquelles il n'y en a eu aucune ;
* **`no_command_stage` reste `None` quand une commande est partie**, sinon le
  champ se remplirait à chaque tour et ne désignerait plus rien.

## La chaîne, une fois complète

```
PYTHON_LOOP -> PLANNER -> CONFIDENCE -> SAFETY -> DUPLICATES -> THROTTLE
            -> TRANSLATION -> MICRO_MOVE -> PUBLISH -> ACK
```

Quatre précautions supplémentaires, chacune fermant une façon de mentir :

* **la sécurité n'est accusée que si elle a vidé le tuyau.** `SafetyEngine.filter`
  peut bloquer une charge suicidaire *et* produire un `HOLD_POSITION` à la place :
  compter les refus faisait lire `stage=safety` alors qu'elle avait fourni cinq
  ordres utilisables, tués plus loin. Seule la **sortie** le dit ;
* **aucun étage par défaut.** Quand les compteurs ne correspondent à aucun chemin
  connu, le verdict est `INVARIANT_VIOLATION`. « Je ne comprends pas, ce doit
  être la sécurité » envoie chercher au mauvais endroit ;
* **un accusé absent n'est pas un accusé accepté.** Les deux rendaient un tuple
  de refus vide : un ordre jamais vu par le Lua était indiscernable d'un ordre
  exécuté ;
* **le diagnostic est écrit dans le journal**, pas seulement affiché. Il ne
  dépend plus d'un terminal resté ouvert.

Et les branches de renoncement du planificateur publient désormais leur code —
`WITHDRAWING`, `DEFEND_WAITING`, `MELEE_ALREADY_ENGAGED`, `NO_FEASIBLE_SECTOR`,
`COMMIT_FAILED`, `ASSAULT_ALREADY_RUNNING` — **produit là où la branche
renonce**, jamais reconstruit après coup. Un motif non nommé reste `UNKNOWN`.

## Ce que cela ne fait pas

Aucun correctif de comportement. L'étage responsable des 364 secondes n'est pas
encore nommé par la mesure — il le sera à la prochaine session, où chaque tour
muet dira où la commande est morte.

Banc inchangé : 82 % sur trois graines, 80 % de forces restantes, aucune
régression. Gate A n'est pas franchie, Gate C reste fermée.

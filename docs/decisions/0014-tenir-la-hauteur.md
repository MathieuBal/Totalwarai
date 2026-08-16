# 0014 — Tenir la hauteur

**Statut :** mesure livrée, deux corrections expédiées, une troisième ajournée — 08/08/2026

## Le problème

L'altitude est **la seule donnée de terrain que le jeu nous donne**. Elle circule
depuis toujours — `position():get_y()`, publiée à chaque état pour chaque unité,
enregistrée depuis `recording.py:367` — et **aucune décision ne la lisait** :
zéro occurrence de `.y` dans tout `agent/`.

Ce que la mesure a trouvé sur les deux batailles réelles enregistrées :

| | écart d'altitude à l'arrivée au contact | pire moment | relief de la carte |
| --- | --- | --- | --- |
| `854ebefb` | **−5,25 m** | −11,5 m | 22,1 m |
| `6f2fd14f` | **−6,46 m** | −11,5 m | 15,6 m |

**L'agent est arrivé au contact en contrebas dans les deux cas.** Il ne
choisissait pas mal la pente : il ne la voyait pas.

Et cela se joue pendant la phase d'approche — les mêmes deux cents secondes où
les journaux le montrent sans un ordre.

## Deux pièges trouvés en construisant la mesure

**Les volantes ne renseignent pas le sol.** L'altitude d'une unité en vol est
celle de son vol : une volante relevée à 222 m quand le terrain alentour est à 60
fausserait tout. Elles sont écartées partout.

**Un seigneur volant n'est ni de rôle `flying_unit` ni étiqueté `flying`** — la
règle du classifieur cède la priorité à `lord` pour ne pas lui retirer sa
protection. Il passe donc au travers du filtre. D'où la **médiane** plutôt que la
moyenne : sur douze unités, un point à deux cents mètres déplace l'une de treize
mètres et l'autre pas du tout. `UnitState.is_airborne` porte désormais ce fait,
dans le domaine, avec l'avertissement.

## Le verdict porte sur l'approche, pas sur la bataille

Sans cette distinction, la mesure **disait le contraire de la vérité**. Sur
`854ebefb` : −11 m pendant l'approche, puis +11 m à la neuvième minute, quand les
unités en déroute refluaient vers la hauteur. La moyenne des deux vaut −2 m et ne
décrit aucun moment de la bataille — le verdict passait à « la hauteur ne nous
était pas défavorable » pour une bataille livrée en contrebas de bout en bout.

Une fois les lignes au contact, l'altitude ne se choisit plus : elle est subie.

## Ce qui est expédié

**`Planner.slope_advantage`** — descendre sur l'ennemi rapporte, monter vers lui
coûte. Nul en deçà de trois mètres, saturé à quinze : une falaise n'est pas douze
fois pire qu'un talus. Poids 0,40, volontairement plus faible que la
concentration (0,80) : le dénivelé modifie une mêlée, le nombre la décide.

**`SuicidalChargeRule` durcit son seuil en montée** — jusqu'à une fois et demie
la force exigée en pente franche. Modeste à dessein : une exigence trop haute
reproduirait le défaut que l'ADR 0013 vient de corriger, une règle de sécurité
qui interdit la manœuvre au lieu de la borner. La sécurité garde **ses propres
seuils** plutôt que d'importer ceux du planificateur : le filet doit pouvoir
juger seul, sinon remplacer le planificateur par un modèle appris emporterait les
garde-fous avec lui.

## Ce que le banc peut en dire : rien, et c'est démontré

Le monde de la doublure est **entièrement plat**. Vérifié plutôt que supposé : sur
quatre scénarios, l'ensemble des altitudes relevées vaut `{0.0}`, et
`slope_advantage` a été appelé **1 022 fois en rendant zéro à chaque fois**.

« Aucune régression au banc » ne signifie donc strictement rien ici — le terme est
prouvé inerte. Les seuls garde-fous sont les tests unitaires, et ils portent
chacun sur un cas mesuré : terrain plat, montée, descente, saturation, unité en
vol.

C'est un cran plus faible que la concentration (ADR 0010) et le contournement
(ADR 0013), où le banc démontrait au moins l'innocuité. Ici il ne démontre même
pas cela.

## Ce qui est ajourné, et pourquoi

**Occuper la hauteur pendant l'approche** était le gain principal attendu. Il est
ajourné.

L'agent ne connaît l'altitude qu'aux points où une unité est passée. L'ancre du
plan est déjà le barycentre de nos propres unités : lui demander de « préférer la
position la plus haute parmi celles que nous occupons » ne la déplace presque pas.
Pour choisir une hauteur à prendre, il faut connaître l'altitude d'un point où
personne n'est allé — c'est-à-dire le relevé de grille.

S'ajoute une raison de prudence : l'ancre est précisément la pièce que l'ADR 0005
documente comme ayant été cassée deux fois, dont une correction qui faisait passer
`balanced_clash` de 100 % à 0 %. La toucher sans instrument de mesure serait
recommencer.

**Le relevé de grille est donc justifié**, et il l'est désormais par une mesure et
non par une intuition. `v_to_ground(v(x,0,z)):get_y()` lit le relief en tout point
— 14,9 à 40,9 m relevés sur une croix de 300 m. Il reste que le recensement
**journalise sans publier** : aucun message de protocole ne transporte ses
valeurs, et les exploiter demandera une nouvelle révision de la sonde, donc un
repack.

## La mesure qui tranchera

`learn --units`, section « qui tenait la hauteur », sur la prochaine bataille
jouée. L'**écart avant contact** doit quitter −5 m et −6,5 m.

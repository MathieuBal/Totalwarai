# 0013 — Un contournement n'est pas une charge frontale

**Statut :** retenu — gain faible mais sans coût, manœuvre débloquée — 08/08/2026

## Le problème

`ActionType.FLANK` appartient à `CHARGE_ACTIONS`
(`src/totalwar_ai/domain/actions.py:63`). Tout contournement passait donc par
`SuicidalChargeRule`, qui compare la force alliée présente **autour de la cible**
à la force ennemie locale dans un rayon de 60 m, et refuse en dessous de 0,6.

Or le planificateur envoie la cavalerie sur les **tireurs adverses** — le rôle
classique de la cavalerie de choc. Ces tireurs se tiennent derrière leur ligne.
Compter tous les ennemis à 60 m de la cible revient donc à compter cette ligne
entière, tandis que la cavalerie, encore loin, n'apporte qu'elle-même. **Le
rapport était perdu d'avance quelle que soit la situation.**

Mesure en bataille `a1274d62` : **trente-cinq refus de sécurité, tous des
contournements**. La règle remplace l'ordre par `HOLD_POSITION` — la cavalerie et
les deux volantes ont donc tenu la position pendant toute la phase d'approche.
Elles n'ont été libérées qu'une fois la cible au contact (exemption
`target.is_engaged`), c'est-à-dire trop tard : elles ont alors oscillé entre dix
cibles en cent trente secondes, parcouru 1 944 m contre 500 à 800 pour le reste
de l'armée, et **rompu les premières**, ouvrant la cascade.

## Ce qui menace réellement une cavalerie de flanc

Pas la ligne adverse à 60 m — elle fait face ailleurs, c'est tout l'intérêt du
contournement — mais **l'escorte immédiate de la cible**, celle qui peut se
retourner sur elle. D'où deux rayons au lieu d'un :

* charge frontale : `local_power_radius` = 60 m, inchangé ;
* contournement : `flank_escort_radius` = 35 m.

Le veto lui-même n'est pas affaibli : une cavalerie lancée sur un tireur **collé
à trois unités de mêlée** reste refusée, et un test le vérifie.

## Ce que la mesure a dit

Banc autonome, onze scénarios, huit graines (11, 23, 37, 101–105) :

| rayon d'escorte | victoires | forces | seigneur |
| --- | --- | --- | --- |
| 60 m (avant) | 78 % | 77 % | 100 % |
| 45 m | 78 % | 77 % | 100 % |
| **35 m** | **82 %** | 77 % | 100 % |
| 25 m | 73 % | 76 % | 100 % |

Le pic est net et encadré : trop large, le contournement reste interdit ; trop
étroit, la cavalerie part se faire prendre.

> **Le compte de refus n'a pas baissé** — 258 à 290. C'est contre-intuitif et
> instructif : libérer les contournements change toute la trajectoire des
> batailles et produit d'autres événements de sécurité ailleurs. Le nombre de
> refus n'est donc pas un indicateur du défaut, seulement un symptôme de la
> bataille jouée.

### Validation sur graines inédites

Un pic mesuré sur le jeu de graines qui a servi au réglage peut n'être qu'un
ajustement au bruit — quatre points sur quatre-vingt-huit batailles font trois
batailles et demie. La mesure a donc été rejouée sur dix graines **jamais
utilisées** (201–210).

| rayon d'escorte | victoires (graines inédites) |
| --- | --- |
| 60 m | 81 % |
| **35 m** | **82 %** |

**Un point, pas quatre.** Le pic mesuré sur les graines de réglage était pour
l'essentiel du bruit, et il faut le dire : quatre points sur quatre-vingt-huit
batailles, c'est trois batailles et demie, exactement l'ordre de grandeur d'un
hasard favorable. La validation ramène le gain à un point, qui n'est lui-même pas
distinguable de zéro.

Ce qui reste solide après validation :

* le changement **ne coûte rien** — victoires, forces restantes et survie du
  seigneur inchangées ou meilleures sur les deux jeux de graines ;
* il **débloque une manœuvre qui était structurellement impossible**, ce
  qu'aucun taux de victoire ne mesure sur une doublure sans moral ni terrain.

C'est le même raisonnement que pour la concentration (ADR 0010) : on expédie sur
une preuve d'innocuité, pas sur une preuve de bénéfice, et l'on dit lequel des
deux on a.

> Cette validation existe parce que la mesure de réglage seule aurait fait
> publier « +4 points ». **Un réglage se valide sur des graines qui n'ont pas
> servi à le choisir**, sans quoi l'on mesure sa propre recherche.

## Ce que cela ne prouve pas

La doublure du banc n'a ni moral ni terrain, et sa cavalerie manœuvre dans un
monde plat. Le gain mesuré ici dit que la règle ne nuit pas et qu'elle libère une
manœuvre auparavant impossible ; il ne dit pas que le contournement sera bien
conduit en jeu. La mesure qui tranchera est le journal de `--play` : les
contournements doivent cesser d'être refusés en masse, et la cavalerie doit
cesser de parcourir le double de la distance des autres.

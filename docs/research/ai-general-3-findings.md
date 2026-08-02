# Ce qu'AI General 3 apprend sur les API de bataille

> **Nature de ce document.** Notes de recherche rédigées en observant un mod
> tiers, *AI General 3* (auteur : paperpancake), fourni localement à titre de
> documentation. Ce dépôt **ne contient et ne redistribue aucun fichier de ce
> mod** : ni `.pack`, ni scripts Lua, ni fichiers d'interface, ni code MCT.
>
> Ce qui suit décrit **les API du jeu** telles que ce mod démontre qu'elles
> existent et fonctionnent. Aucune portion de code tiers n'est reproduite ici :
> les signatures citées sont celles de l'API publique de Creative Assembly, pas
> l'implémentation du mod. Le script de `lua_mod/` est une écriture originale.

## Pourquoi ce mod est une source utile

Il fait tourner, en production et chez des milliers de joueurs, exactement les
briques dont TotalWarAI a besoin : prendre le contrôle d'unités du joueur pendant
une bataille de campagne, leur donner des ordres, et rendre la main. Sa seule
existence répond par l'affirmative à la question « le jeu permet-il de scripter
le contrôle d'unités alliées en campagne solo ? ».

## Objets et méthodes identifiés

### Accès à l'armée du joueur

| Élément | Rôle |
| --- | --- |
| `bm` | le *battle manager*, objet global du contexte de bataille |
| `bm:alliances():item(bm:local_alliance())` | l'alliance du joueur local |
| `alliance:armies():item(bm:local_army())` | l'armée du joueur local |
| `army:units()`, `:count()`, `:item(i)` | itération sur les unités |

Les collections sont indexées **à partir de 1**, convention Lua.

### Interrogation d'une unité

| Méthode | Constat |
| --- | --- |
| `unit:unique_ui_id()` | identifiant stable d'interface — le plus utilisé dans le mod (69 occurrences), c'est la clé d'identification retenue |
| `unit:type()` | clé d'unité (`emp_spearmen`…). **Attention** : il n'existe pas de `unit:unit_key()` |
| `unit:position()` | vecteur, lu via `:get_x()`, `:get_y()`, `:get_z()` |
| `unit:is_controllable()` | l'unité peut-elle recevoir des ordres scriptés |
| `unit:is_valid_target()` | l'unité est-elle vivante et pertinente |
| `unit:ammo_left()`, `:missile_range()`, `:can_fly()`, `:is_currently_flying()`, `:has_attribute()`, `:is_idle()`, `:bearing()`, `:fast_speed()` | l'essentiel de ce que `UnitState` demande semble accessible |

Ce dernier point est encourageant pour la suite : le schéma `UnitState` du
protocole complet n'est pas hors d'atteinte. Le moral et la fatigue restent à
confirmer — le mod ne les lit pas.

### Prise de contrôle

| Étape | Appel |
| --- | --- |
| créer un contrôleur | `army:create_unit_controller()` |
| y placer des unités | `uc:add_units(unit, …)` |
| ordonner un déplacement | `uc:goto_location(v_to_ground(v(x, y, z)), courir)` |
| ordonner une attaque | `uc:attack_unit(cible, arme_principale, courir)` |
| stopper | `uc:halt()` |
| **rendre la main** | `uc:release_control()` |
| comportements | `uc:change_behaviour_active("fire_at_will" \| "skirmish", actif)` |

Deux enseignements de fiabilité tirés des commentaires du mod :

1. `uc:add_units()` **peut échouer** si l'unité appartient à un groupe verrouillé.
   Le mod l'entoure d'un `pcall`. La sonde fait de même.
2. Toutes les fonctions ne sont pas disponibles à tout moment de la boucle de jeu.
   Le mod contourne en différant les appels via un `bm:callback` à 0 ms.

### Cadencement

| Appel | Usage |
| --- | --- |
| `bm:callback(fn, ms, nom)` | exécution différée |
| `bm:repeat_callback(fn, ms, nom)` | exécution périodique |
| `bm:remove_process(nom)` | annulation. **Attention** : ce n'est pas `remove_callback` |
| `bm:register_phase_change_callback("Startup" \| "Deployed" \| "Complete", fn)` | jalons de bataille |
| `bm:time_elapsed_ms()` | horloge de bataille |

### Multijoueur

`bm:is_multiplayer()` existe et sert précisément à désactiver ce genre de mod.

## Le point décisif : les entrées-sorties fichier

C'est la question qui conditionne toute l'architecture du pont, et la réponse
observée est **asymétrique**.

| Sens | Statut observé chez le mod tiers | Conséquence pour nous |
| --- | --- | --- |
| **Lecture** en contexte **bataille** | **Démontrée.** Le mod lit sa configuration via `io.open(chemin, "r")` depuis un module chargé par son script de bataille. | Python → Lua : voie sûre. |
| **Écriture** en contexte **bataille** | **Non démontrée.** Le mod n'écrit que depuis le contexte *frontend* (menu principal), jamais pendant une bataille. | Lua → Python : **risque principal du prototype.** |

Les chemins employés sont **relatifs au répertoire de travail du jeu** (de la
forme `./mod_config/…`), et non à un dossier de sauvegarde utilisateur. La sonde
adopte la même convention avec `./totalwar_ai/`.

**Conséquence de conception.** La sonde teste explicitement son droit d'écriture
au premier message, une fois pour toutes, et publie de toute façon chaque message
dans le journal du jeu via `out()`, préfixé `[totalwar_ai]`. Si l'écriture
s'avère impossible en bataille, le journal reste un canal de repli exploitable —
en lecture seule et avec un délai, mais exploitable.

## Mécanismes repérés, hors périmètre du prototype

Notés pour les phases ultérieures, pas implémentés :

- **coordination entre mods** : le mod maintient un registre des unités
  revendiquées pour éviter que deux mods se disputent le même contrôleur. Si
  TotalWarAI devait cohabiter avec d'autres mods de contrôle, il faudrait un
  mécanisme équivalent ;
- **renforts, transformations, invocations** : ces unités apparaissent en cours
  de bataille et cassent toute liste établie au démarrage. Le mod y consacre son
  plus gros module. Notre agent devra reconstruire ses groupes à chaque plan —
  ce qu'il fait déjà ;
- **`script_ai_planner`** : l'ordonnanceur d'IA de CA, que le mod désactive sur
  les unités qu'il prend en charge. À comprendre avant de piloter une armée
  entière ;
- **MCT** (*Mod Configuration Tool*) : interface de configuration tierce. Hors
  périmètre : notre configuration est en YAML côté Python.

## Ce que ce document ne dit pas

Rien de ceci n'a été vérifié par nos soins dans une bataille réelle. Ce sont des
constats de lecture sur un mod tiers, qui orientent la conception mais ne la
valident pas. L'état des vérifications se tient dans
[`../feasibility.md`](../feasibility.md).

## Seconde relecture ciblée (02/08/2026)

Faite en réponse à une question légitime : pourquoi découvrir en jeu ce qu'un
mod avancé sait déjà ? Recherche par motifs sur les questions restées ouvertes.
**Aucun code n'est repris** — seuls les noms d'API et les mécanismes sont
consignés, en nos propres termes.

### Ce que la relecture a apporté

| Question ouverte | Réponse trouvée |
| --- | --- |
| identifier le seigneur | **`unit:is_commanding_unit()`**. Le mod commente avoir longtemps supposé que la première unité de l'armée était le seigneur, et garde cette supposition en repli |
| ordre d'attaque | `uc:attack_unit(cible, primaire, courir)`, précédé au besoin de `uc:melee(true)` pour forcer le corps à corps |
| comportements | `unit:can_use_behaviour(nom)`, `unit:is_behaviour_active(nom)`, `uc:change_behaviour_active(nom, actif)`. Noms constatés : **`fire_at_will`**, **`skirmish`** |
| prendre plusieurs unités | `uc:add_units()` accepte **plusieurs unités à la fois**. Le mod l'enveloppe dans un `pcall` en notant que l'appel échoue si l'unité est dans un groupe verrouillé |
| unité inactive | `unit:is_idle()` |

### Ce que la relecture a montré comme impasses

**Le moral et la fatigue n'apparaissent nulle part dans ce mod** — zéro
occurrence de `morale`, `fatigue`, `exhaust` ou `tired` dans l'intégralité du
`.pack`. Il ne les lit pas. Notre recensement en bataille avait déjà conclu à
leur absence ; la relecture confirme qu'il n'y avait rien à en apprendre.

**`land_units` n'est pas une API de bataille.** Le mod y fait référence dans ses
commentaires — « depuis `land_units_table`, où munitions primaires > 0 et
AiUsage contient melee » — pour documenter **des listes codées en dur**, obtenues
en interrogeant la base de données hors du jeu. Il n'existe donc pas d'accès à
ces statistiques depuis un script de bataille. Notre approche — déduire le rôle
de ce que le jeu mesure (`missile_range`, `ammo_left`) — reste la bonne, et se
trouve même être plus robuste qu'une liste figée.

### Ce que la relecture ne pouvait pas éviter

Pour être juste envers la question posée, voici l'origine réelle des difficultés
rencontrées jusqu'ici :

| Difficulté | Le mod pouvait-il l'éviter ? |
| --- | --- |
| `math.huge` absent du bac à sable | **Non** — il n'utilise pas `math.huge` |
| écriture de fichier en bataille | **Non** — il n'écrit que depuis le frontend, ce qui était déjà consigné ici comme le risque principal |
| moral et fatigue inaccessibles | **Non** — il ne les lit pas |
| munitions données en total | **Non** — il ne s'en sert pas |
| décalage d'offset sur fins de ligne Windows | **Non** — défaut Python, sans rapport |
| séquence non reprise entre processus | **Non** — défaut Python, sans rapport |
| commande survivant à la bataille | **Non** — conception propre à notre pont |
| identification du seigneur | **Oui** — perdu faute d'avoir cherché plus tôt |
| ordres d'attaque et comportements | **Oui** — évite un tâtonnement à venir |

La leçon n'est pas « il fallait tout lire d'emblée », mais **« relire le code
tiers dès qu'une question précise se pose »**. Une recherche ciblée sur une
question formulée trouve en quelques minutes ce qu'une lecture exhaustive
préalable aurait noyé.

## La découverte qui change le projet (02/08/2026)

**AI General 3 ne calcule pas de tactique. Il délègue à l'IA du jeu.**

Son code le dit littéralement :

```
script_ai_planner:new("pancake_aigeneral", list_of_sus_to_use, is_debug)
-- "units that currently haven't been given to the AI"
-- "this gives *everything* to the AI"
```

Toute l'API tient en cinq appels : `new`, `add_sunits`, `remove_sunits`,
`release`, `ensure_units_are_released`. Les `script_unit` s'obtiennent par
`bm:get_scriptunit_for_unit(unit)`, ou se créent par `script_unit:new(unit)`
lorsque le jeu n'en tient pas — cas des invocations et des transformations, qui
peut lever une erreur de script, d'où un `pcall`.

### Pourquoi cela compte

L'IA de bataille de Creative Assembly connaît le terrain, le pathfinding, les
statistiques d'unités et les formations. **Tout ce que notre recensement a
montré inaccessible à un script Lua** : ni moral, ni fatigue, ni vitesse, ni
largeur de front, et aucune donnée de terrain.

Un mod qui délègue hérite donc de toutes ces informations sans les lire. Un
agent qui décide lui-même doit s'en passer. Ce n'est pas une différence de
soin ou de vitesse de développement : ce sont deux problèmes de difficulté très
inégale, et il fallait le dire plus tôt.

### Ce que le mod calcule quand même

La délégation ne fait pas tout. Le mod garde pour lui :

* le choix des unités à confier, et le moment de les reprendre — c'est là que
  vit sa configuration (`ALL_BUT_LORD`, renforts seulement, etc.) ;
* un module de poursuite des fuyards, avec appariement des poursuivants aux
  cibles (`_assign_all`, `prev_melee_target_table`) ;
* la gestion du tir à volonté et de l'escarmouche (`fire_at_will`, `skirmish`),
  mémorisée puis restaurée quand une unité est rendue au joueur.

### Ce que nous en avons fait

La délégation est implémentée dans notre sonde (`delegate` / `reclaim`), en
appelant **directement les API du moteur** — aucune dépendance à AI General, qui
n'a pas besoin d'être installé.

Elle sert deux buts : donner immédiatement un mod qui joue vraiment, et fournir
la **référence de comparaison** qui manquait à notre agent. L'ADR 0005 constate
que le simulateur et le jeu se contredisent sans qu'on puisse les départager ;
l'IA du jeu, elle, joue dans le jeu.

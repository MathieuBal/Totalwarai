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

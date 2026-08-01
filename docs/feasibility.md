# Faisabilité de l'intégration au jeu (Phase 0)

> **Statut : premier essai en bataille effectué le 01/08/2026. Le script n'a
> pas été chargé par le jeu.**
>
> L'essai n'a donc validé aucune API de contrôle, mais il a produit des
> informations réelles sur l'environnement de bataille, consignées ci-dessous.
> Il a aussi révélé un défaut de la sonde elle-même — elle n'émettait aucune
> preuve de chargement — désormais corrigé.
>
> Tant que la section [Résultats](#résultats-de-lessai-en-bataille) reste
> incomplète, aucun comportement en jeu ne doit être présenté comme fonctionnel.

## Légende

| Marque | Sens |
| --- | --- |
| **non testée** | jamais exécuté dans le jeu |
| **accessible** | vérifié en bataille, fonctionne |
| **partiellement accessible** | vérifié en bataille, avec des réserves à décrire |
| **inaccessible** | vérifié en bataille, ne fonctionne pas |

## Environnement testé

Relevé lors de l'essai du 01/08/2026 (`script_log_010826_2114.txt`) :

| Élément | Valeur observée |
| --- | --- |
| Version de Lua | **5.1** (confirmé : `Lua version is Lua 5.1`) |
| Système d'exploitation | Windows (chemins `D:\SteamLibrary\…`) |
| Script de bataille | aucun défini, script par défaut chargé (`script\battle\default_battle\battle_start.lua`) |
| Type de bataille | bataille sans script dédié, avec « Battle Fundamentals scripted tour » actif |
| Mods chargés | `script\_lib\mod\qa_console.lua`, `script\_lib\mod\test_script_here.lua` — **aucun mod utilisateur** |
| Unités présentes | 22 `script_unit` : alliance 1 armée 1 = 11 unités (joueur), alliance 2 armée 1 = 11 unités |

## Observation — ce que le Lua expose

Colonne « attendu » : ce que la lecture d'un mod tiers laisse espérer
(voir [`research/ai-general-3-findings.md`](research/ai-general-3-findings.md)).
Colonne « en bataille » : ce que nous avons constaté nous-mêmes.

Le journal de l'essai contient les traces du système de visite guidée de
Creative Assembly, qui affiche identifiant, type et position de chaque unité.
Ce n'est pas notre code qui les a produites, mais cela **confirme que ces
données existent et sont lisibles par un script de bataille** :

```text
1: 1006 of type wh3_main_nur_inf_plaguebearers_1 at position [25.6, 21.0, -303.4]
```

| Donnée | API pressentie | Attendu | En bataille |
| --- | --- | --- | --- |
| liste des unités alliées | `alliance:armies():item():units()` | accessible | **partiellement accessible** — le jeu recense bien 11 unités alliées, non vérifié par notre code |
| identifiant stable | `unit:unique_ui_id()` | accessible | **partiellement accessible** — identifiants numériques visibles (1003, 1005, 1006…) |
| clé d'unité | `unit:type()` | accessible | **partiellement accessible** — `wh3_main_nur_inf_plaguebearers_1` visible dans le journal |
| position (x, y, z) | `unit:position():get_x()` … | accessible | **partiellement accessible** — triplets `[x, y, z]` visibles, y = altitude non nulle (21 à 33) |
| unité contrôlable | `unit:is_controllable()` | accessible | **non testée** |
| unité vivante | `unit:is_valid_target()` | accessible | **non testée** |
| temps de jeu | `bm:time_elapsed_ms()` | accessible | **non testée** |
| phases de bataille | `bm:register_phase_change_callback` | accessible | **accessible** — `Startup`, `PrebattleWeather`, `PrebattleCinematic`, `Deployment`, `Deployed` observées dans le journal |
| cap / orientation | `unit:bearing()` | probable | **non testée** |
| munitions | `unit:ammo_left()` | probable | **non testée** |
| portée de tir | `unit:missile_range()` | probable | **non testée** |
| capacité de vol | `unit:can_fly()` | probable | **non testée** |
| moral | inconnue | inconnu | **non testée** |
| fatigue | inconnue | inconnu | **non testée** |
| effectifs restants | inconnue | inconnu | **non testée** |
| unités ennemies visibles | `bm:get_non_player_alliance()` | probable | **non testée** |
| unité en déroute | inconnue | inconnu | **non testée** |
| unité cachée | inconnue | inconnu | **non testée** |

Les lignes « inconnue » sont celles que le mod étudié ne lit pas : rien ne dit
qu'elles sont indisponibles, seulement que nous n'avons aucun indice.

## Commande — ce que le Lua permet d'ordonner

| Ordre | API pressentie | Attendu | En bataille |
| --- | --- | --- | --- |
| créer un contrôleur | `army:create_unit_controller()` | accessible | **non testée** |
| prendre une unité | `uc:add_units(unit)` | accessible, peut échouer sur groupe verrouillé | **non testée** |
| déplacer vers un point | `uc:goto_location(v_to_ground(v(x,y,z)), run)` | accessible | **non testée** |
| attaquer une unité | `uc:attack_unit(cible, primaire, courir)` | accessible | **non testée** |
| stopper | `uc:halt()` | accessible | **non testée** |
| **rendre la main** | `uc:release_control()` | accessible | **non testée** |
| comportements (tir à volonté…) | `uc:change_behaviour_active()` | accessible | **non testée** |

## Communication Lua ↔ Python

C'est le point qui décide de tout le reste.

| Sens | Mécanisme | Attendu | En bataille |
| --- | --- | --- | --- |
| Python → Lua (commande) | `io.open(chemin, "r")` | **accessible** : un mod tiers lit sa configuration ainsi depuis un script de bataille | **non testée** |
| Lua → Python (état, accusé) | `io.open(chemin, "a")` | **incertain** : le mod étudié n'écrit que depuis le frontend, jamais en bataille | **non testée** |
| Lua → Python (repli) | `out()` vers le journal du jeu | accessible | **non testée** |

**C'est le risque principal du prototype.** Si l'écriture est refusée en
contexte bataille, l'aller-retour complet par fichiers tombe, et il faudra se
rabattre sur la lecture du journal du jeu — plus lente, en lecture seule, mais
suffisante pour observer. La sonde teste explicitement ce droit au premier
message et écrit `ECRITURE INDISPONIBLE` dans le journal si elle échoue.

## Protocole d'essai

Ce qu'il faut faire, dans l'ordre, pour remplir les cases ci-dessus.

### Préparation

1. Empaqueter `lua_mod/script/battle/mod/totalwar_ai_probe.lua` dans un `.pack`
   de type *mod* (RPFM). **Placer le fichier aux deux emplacements suivants**,
   pour lever l'ambiguïté constatée au premier essai :

   ```text
   script/_lib/mod/totalwar_ai_probe.lua      <- emplacement prouvé chargé
   script/battle/mod/totalwar_ai_probe.lua    <- emplacement à confirmer
   ```

   Le script se protège contre le double chargement : le second exemplaire
   rencontré s'annonce puis s'arrête. Aucun risque à mettre les deux.

   Aucun dossier ne doit précéder `script` dans l'arborescence du pack. Le
   fichier doit garder l'extension `.lua`, pas `.lua.txt`.

2. Le déposer dans `<installation>/data/` et l'activer dans le gestionnaire de
   mods. Fermer complètement le jeu et son launcher avant toute modification du
   `.pack`.
3. Créer le dossier `<installation>/totalwar_ai/`.
4. Côté Python :

   ```bash
   export TOTALWAR_AI_BRIDGE_DIR="<installation de WARHAMMER III>"
   totalwar-ai probe --status      # doit afficher le dossier, tout absent
   ```

### Essai, en campagne solo uniquement

1. Lancer une bataille de campagne et la laisser démarrer (phase de déploiement
   terminée).
2. Ouvrir le journal de script du jeu et chercher `[totalwar_ai]`. **Noter la
   première ligne** : elle dit si l'écriture de fichier est disponible.
3. Côté Python :

   ```bash
   totalwar-ai probe --watch 30
   ```

   Doit afficher l'identifiant, le type et la position d'une unité.
4. Ordonner un déplacement d'environ vingt mètres :

   ```bash
   totalwar-ai probe --move 20
   ```

5. Observer dans le jeu : l'unité doit se mettre en mouvement, puis redevenir
   contrôlable par le joueur au bout de cinq secondes.
6. Vérifier la non-répétition : relancer `totalwar-ai probe --status` sans
   nouvelle commande ; aucune nouvelle exécution ne doit avoir lieu côté jeu.
7. Vérifier l'arrêt d'urgence :

   ```bash
   totalwar-ai probe --abort
   ```

   Le journal doit indiquer `SONDE ARRETEE`, et toute unité prise doit être
   rendue au joueur.

### En cas d'échec

| Symptôme | Piste |
| --- | --- |
| aucune ligne `=== fichier charge ===` dans le journal | le jeu ne trouve pas le fichier. Vérifier : pack activé, aucun dossier avant `script` dans l'arborescence, extension `.lua` et non `.lua.txt`, pack modifié jeu fermé |
| `fichier charge` présent, mais `pas de battle_manager` | le fichier est bien chargé mais hors bataille (menu, campagne). Normal : relancer une bataille |
| `fichier charge` présent, puis plus rien | la sonde est chargée mais `start()` échoue. Chercher une erreur de script juste après |
| `ECRITURE INDISPONIBLE` | c'est le résultat attendu du risque principal : consigner le message d'erreur exact, et basculer sur le repli par journal |
| `multijoueur ou type de partie inconnu` | garde-fou volontaire ; vérifier qu'il s'agit bien d'une bataille solo |
| aucun état reçu côté Python | comparer le dossier affiché par `probe --status` et celui où le jeu écrit réellement |
| `uc:add_units a echoue` | l'unité est dans un groupe verrouillé ; en essayer une autre |

## Résultats de l'essai en bataille

### Essai n° 1 — 01/08/2026

| Critère du ticket | Résultat | Notes |
| --- | --- | --- |
| le script Lua est chargé par le jeu | **échec** | aucune ligne `[totalwar_ai]` dans le journal |
| une unité réelle est détectée | **non testé** | bloqué par le point précédent |
| sa position est transmise à Python | **non testé** | idem |
| une commande Python est lue par Lua | **non testé** | idem |
| l'unité se déplace réellement | **non testé** | idem |
| un accusé est reçu par Python | **non testé** | idem |
| la commande ne peut pas être exécutée deux fois | **non testé** | idem |
| le joueur récupère le contrôle | **non testé** | idem |

**Ce que le journal montre.** Le bloc de chargement des mods ne contient que
deux fichiers, tous deux fournis par le jeu :

```text
****************************
Loading Mods
	Loading mod file [script\_lib\mod\qa_console.lua]
	Loading mod file [script\_lib\mod\test_script_here.lua]
****************************
```

Deux lectures étaient possibles, et **le journal ne permettait pas de trancher** :

1. le `.pack` n'était pas chargé du tout (arborescence interne incorrecte,
   extension `.lua.txt`, mod non activé, ou pack modifié jeu ouvert) ;
2. le répertoire `script/battle/mod/` n'est pas balayé par ce chargeur — seul
   `script/_lib/mod/` apparaît dans ce bloc.

L'hypothèse 2 est affaiblie par le fait qu'un mod tiers largement utilisé place
son script de bataille dans `script/battle/mod/` et fonctionne. Il se peut que ce
répertoire soit chargé par un autre mécanisme, sans trace dans ce bloc de journal.

**Défaut de la sonde révélé par cet essai.** Elle n'émettait aucune preuve de vie
au chargement : impossible de distinguer les deux hypothèses. Corrigé — la
première ligne exécutée du fichier écrit désormais :

```text
[totalwar_ai] === fichier charge (sonde v0.1.0) ===
```

Cette ligne apparaît quel que soit le contexte et quoi qu'il advienne ensuite.
Au prochain essai, sa présence ou son absence tranchera :

- **absente** → le jeu ne trouve pas le fichier : problème d'empaquetage ;
- **présente** → le fichier est chargé, le problème est ailleurs, et le message
  suivant (`contexte de bataille detecte` ou `pas de battle_manager`) dira où.

En attendant, le protocole d'essai demande de placer le fichier **aux deux
emplacements**, avec une garde contre le double chargement.

### Journal brut

*Coller ici les lignes `[totalwar_ai]` du prochain essai, et le contenu des trois
fichiers d'échange.*

## Ce qui est vérifié à ce stade

Pour être juste envers le travail déjà fait, voici ce qui **est** établi — et
qui ne concerne que la moitié Python :

- le pont écrit les commandes atomiquement (`os.replace` dans le répertoire
  cible), sans jamais laisser de fichier temporaire, même en cas d'échec ;
- il lit les flux de façon incrémentale et ne relivre pas deux fois le même
  message ;
- une ligne encore en cours d'écriture n'est pas consommée à moitié ;
- une ligne illisible est ignorée et signalée, sans interrompre le flux ;
- le JSON produit par Python est lisible par les motifs d'analyse exacts du
  script Lua — y compris indenté, et sans notation scientifique, un défaut
  trouvé et corrigé pendant l'écriture des tests ;
- les règles de séquence, de refus et d'arrêt d'urgence tiennent, éprouvées
  contre une reproduction en Python de la logique du script.

Voir `tests/unit/test_file_bridge.py` et `tests/integration/test_lua_protocol.py`.
Ces tests ne remplacent pas l'essai en bataille et ne prétendent pas le faire.

## Décision

À prendre à l'issue de l'essai :

- le mécanisme de communication retenu (fichiers, ou repli par journal) ;
- les champs du protocole complet à rendre optionnels ;
- les actions à retirer du périmètre de la première intégration ;
- s'il faut faire évoluer `PROTOCOL_VERSION`.

Toute évolution du protocole doit être reportée dans
[`protocol.md`](protocol.md) avec un incrément de version.

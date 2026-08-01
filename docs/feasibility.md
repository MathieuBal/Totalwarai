# Faisabilité de l'intégration au jeu (Phase 0)

> **Statut : prototype écrit, non essayé en bataille.**
>
> La sonde `lua_mod/script/battle/mod/totalwar_ai_probe.lua` et le pont Python
> `src/totalwar_ai/bridge/file_bridge.py` sont terminés et testés **entre eux**.
> Rien n'a été exécuté dans *Total War: WARHAMMER III* : l'environnement de
> développement de ce dépôt est un conteneur Linux sans le jeu.
>
> En conséquence, **tout ce tableau est en « non testée »** pour la colonne
> « en bataille ». Un comportement validé côté Python n'est pas un comportement
> validé : la section [Protocole d'essai](#protocole-dessai) indique quoi faire
> pour remplir ces cases, et [Résultats](#résultats-de-lessai-en-bataille) où
> les consigner.

## Légende

| Marque | Sens |
| --- | --- |
| **non testée** | jamais exécuté dans le jeu |
| **accessible** | vérifié en bataille, fonctionne |
| **partiellement accessible** | vérifié en bataille, avec des réserves à décrire |
| **inaccessible** | vérifié en bataille, ne fonctionne pas |

## Environnement testé

À renseigner lors de l'essai :

| Élément | Valeur observée |
| --- | --- |
| Version du jeu | |
| Système d'exploitation | |
| Autres mods actifs | |
| Type de bataille (campagne, escarmouche) | |
| Date de l'essai | |

## Observation — ce que le Lua expose

Colonne « attendu » : ce que la lecture d'un mod tiers laisse espérer
(voir [`research/ai-general-3-findings.md`](research/ai-general-3-findings.md)).
Colonne « en bataille » : ce que nous avons constaté nous-mêmes.

| Donnée | API pressentie | Attendu | En bataille |
| --- | --- | --- | --- |
| liste des unités alliées | `alliance:armies():item():units()` | accessible | **non testée** |
| identifiant stable | `unit:unique_ui_id()` | accessible | **non testée** |
| clé d'unité | `unit:type()` | accessible | **non testée** |
| position (x, y, z) | `unit:position():get_x()` … | accessible | **non testée** |
| unité contrôlable | `unit:is_controllable()` | accessible | **non testée** |
| unité vivante | `unit:is_valid_target()` | accessible | **non testée** |
| temps de jeu | `bm:time_elapsed_ms()` | accessible | **non testée** |
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
   de type *mod* (RPFM), en conservant le chemin interne
   `script/battle/mod/totalwar_ai_probe.lua`.
2. Le déposer dans `<installation>/data/` et l'activer.
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
| aucune ligne `[totalwar_ai]` dans le journal | le `.pack` n'est pas chargé, ou le chemin interne est faux |
| `ECRITURE INDISPONIBLE` | c'est le résultat attendu du risque principal : consigner le message d'erreur exact, et basculer sur le repli par journal |
| `multijoueur ou type de partie inconnu` | garde-fou volontaire ; vérifier qu'il s'agit bien d'une bataille solo |
| aucun état reçu côté Python | comparer le dossier affiché par `probe --status` et celui où le jeu écrit réellement |
| `uc:add_units a echoue` | l'unité est dans un groupe verrouillé ; en essayer une autre |

## Résultats de l'essai en bataille

*Section à remplir après le premier essai réel. Tant qu'elle est vide, aucun
comportement en jeu ne doit être présenté comme fonctionnel, où que ce soit dans
ce dépôt.*

| Critère du ticket | Résultat | Notes |
| --- | --- | --- |
| le script Lua est chargé par le jeu | **non testé** | |
| une unité réelle est détectée | **non testé** | |
| sa position est transmise à Python | **non testé** | |
| une commande Python est lue par Lua | **non testé** | |
| l'unité se déplace réellement | **non testé** | |
| un accusé est reçu par Python | **non testé** | |
| la commande ne peut pas être exécutée deux fois | **non testé** | |
| le joueur récupère le contrôle | **non testé** | |

### Journal brut

*Coller ici les lignes `[totalwar_ai]` du journal du jeu, et le contenu des trois
fichiers d'échange après l'essai.*

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

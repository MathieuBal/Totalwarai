# Faisabilité de l'intégration au jeu (Phase 0)

> **Statut : quatre essais en bataille. L'aller-retour complet fonctionne.**
>
> Essai n° 4 (01/08/2026, 22 h 42) : Python a publié un ordre, le Lua l'a lu et
> exécuté, l'unité `1001` s'est déplacée de **20,3 m** (x = 6,644 → 26,9 mesuré
> par le jeu), et l'accusé est remonté. Les dix étapes du ticket sont franchies.
>
> Restent à confirmer visuellement, faute de preuve dans les relevés : la
> restitution du contrôle au joueur après cinq secondes, et l'arrêt d'urgence.
> Tant que ces deux lignes ne sont pas cochées dans la section
> [Résultats](#résultats-de-lessai-en-bataille), elles ne doivent pas être
> présentées comme fonctionnelles.

## Légende

| Marque | Sens |
| --- | --- |
| **non testée** | jamais exécuté dans le jeu |
| **accessible** | vérifié en bataille, fonctionne |
| **partiellement accessible** | vérifié en bataille, avec des réserves à décrire |
| **inaccessible** | vérifié en bataille, ne fonctionne pas |

## Environnement testé

Relevé lors des essais du 01/08/2026 :

| Élément | Valeur observée |
| --- | --- |
| Version de Lua | **5.1** (confirmé : `Lua version is Lua 5.1`) |
| Système d'exploitation | Windows (chemins `D:\SteamLibrary\…`) |
| Script de bataille | aucun défini, script par défaut chargé (`script\battle\default_battle\battle_start.lua`) |
| Type de bataille | bataille sans script dédié, avec « Battle Fundamentals scripted tour » actif |
| Mods chargés | `script\_lib\mod\qa_console.lua`, `script\_lib\mod\test_script_here.lua` — **aucun mod utilisateur** |
| Unités présentes | 22 `script_unit` : alliance 1 armée 1 = 11 unités (joueur), alliance 2 armée 1 = 11 unités |
| Répertoire de travail | le dossier d'installation : `./totalwar_ai/` créé à la main y est bien trouvé |
| Bibliothèque `math` | **restreinte** — `math.huge` vaut `nil` (essai n° 3). Ne rien supposer du reste : la sonde n'utilise plus `math` du tout |

**Sur le bac à sable Lua.** Le jeu n'expose pas une bibliothèque standard
complète. `math.huge` est absent ; `io.open` est présent. Il n'y a pas de liste
publiée de ce qui subsiste, donc la règle de conduite est de n'employer que ce
qui a été vu fonctionner, et de faire tourner le script contre un environnement
volontairement amputé avant chaque essai (voir `restricted_math` dans
`tests/integration/test_lua_probe_execution.py`).

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
| liste des unités alliées | `alliance:armies():item():units()` | accessible | **accessible** — notre code recense 11 unités (essai n° 3) |
| identifiant stable | `unit:unique_ui_id()` | accessible | **accessible** — notre code lit `1001` (essai n° 3) |
| clé d'unité | `unit:type()` | accessible | **accessible** — appelée sans erreur par notre code (essai n° 3) |
| position (x, y, z) | `unit:position():get_x()` … | accessible | **accessible** — les trois accesseurs répondent (essai n° 3) ; y = altitude non nulle (21 à 33) |
| unité contrôlable | `unit:is_controllable()` | accessible | **accessible** — vraie pour 11 unités sur 11 en phase de déploiement |
| unité vivante | `unit:is_valid_target()` | accessible | **accessible** — vraie pour la première unité retenue |
| temps de jeu | `bm:time_elapsed_ms()` | accessible | **accessible** — `game_time_ms` present dans chaque etat, essai n° 4 |
| accès au battle_manager | `bm` global | accessible | **accessible** — `contexte de bataille detecte` au chargement |
| type de partie | `bm:is_multiplayer()` | accessible | **accessible** — appelée sans erreur, a autorisé le démarrage |
| rappels périodiques | `bm:repeat_callback()` | accessible | **accessible** — acceptée sans erreur au démarrage |
| phases de bataille | `bm:register_phase_change_callback` | accessible | **accessible** — `Startup`, `PrebattleWeather`, `PrebattleCinematic`, `Deployment`, `Deployed` observées dans le journal |
| cap / orientation | `unit:bearing()` | probable | **accessible** — `0.972` (essai n° 6) |
| munitions | `unit:ammo_left()` | probable | **accessible** — `0` sur une unité de mêlée, cohérent |
| portée de tir | `unit:missile_range()` | probable | **accessible** — `0` sur une unité de mêlée, cohérent |
| capacité de vol | `unit:can_fly()` | probable | **accessible** — `true` sur un prince démon, correct |
| **moral** | `unit:unary_morale()` | inconnu | **inaccessible** — la méthode n'existe pas |
| **fatigue** | quatre noms tentés | inconnu | **inaccessible** — `fatigue`, `unary_fatigue`, `fatigue_level` absents |
| effectifs restants | `unit:number_of_men_alive()` | inconnu | **accessible** — `number_of_men` absent, mais `unary_hitpoints` donne 0–1 |
| unité en déroute | `unit:is_routing()` | inconnu | **accessible** — `false` |
| unité brisée | `unit:is_shattered()` | inconnu | **accessible** — `false` |
| unité au contact | `unit:is_in_melee()` | inconnu | **accessible** — `false` |
| unité cachée | `unit:is_hidden()` | inconnu | **accessible** — `false` |
| unités ennemies | `bm:alliances()` | probable | **accessible** — 2 alliances, la 2ᵉ aligne 11 unités (essai n° 6) |
| vitesse, largeur de front | `speed`, `width` | inconnu | **inaccessible** — absents |
| nom affichable | `unit:name()` | inconnu | **inutilisable** — renvoie `"1"`, un identifiant de script |

Relevé par le recensement automatique de l'essai n° 6, qui appelle chaque
accesseur candidat une fois sous `pcall` et journalise le résultat.

### Deux manques qui pèsent sur la conception

**Le moral n'est pas lisible.** `is_routing` et `is_shattered` donnent le
*résultat* d'une rupture, jamais la jauge qui la précède. L'agent ne pourra donc
pas anticiper une déroute — seulement la constater. Toute règle du simulateur
qui s'appuie sur un moral continu est inapplicable en jeu telle quelle.

**La fatigue est invisible.** Les quatre noms tentés sont absents. Les décisions
qui en dépendent (relever une unité épuisée, choisir le moment d'une charge)
n'ont aucune donnée sur quoi s'appuyer.

Ces deux manques ne sont pas des bogues à corriger : ce sont des contraintes du
terrain. Ils devront être reportés dans les règles de l'agent avant tout
pilotage réel, faute de quoi il déciderait sur des champs constamment vides.

## Commande — ce que le Lua permet d'ordonner

| Ordre | API pressentie | Attendu | En bataille |
| --- | --- | --- | --- |
| créer un contrôleur | `army:create_unit_controller()` | accessible | **accessible** — essai n° 4 |
| prendre une unité | `uc:add_units(unit)` | accessible, peut échouer sur groupe verrouillé | **accessible** — essai n° 4, sur le seigneur ; le cas du groupe verrouillé reste non rencontré |
| déplacer vers un point | `uc:goto_location(v_to_ground(v(x,y,z)), run)` | accessible | **accessible** — 20,3 m parcourus, essai n° 4 |
| attaquer une unité | `uc:attack_unit(cible, primaire, courir)` | accessible | **non testée** — hors périmètre du prototype |
| stopper | `uc:halt()` | accessible | **non testée** |
| **rendre la main** | `uc:release_control()` | accessible | **non confirmée** — appelée sans erreur, effet non constaté |
| comportements (tir à volonté…) | `uc:change_behaviour_active()` | accessible | **non testée** |

## Communication Lua ↔ Python

C'est le point qui décide de tout le reste.

| Sens | Mécanisme | Attendu | En bataille |
| --- | --- | --- | --- |
| Python → Lua (commande) | `io.open(chemin, "r")` | **accessible** : un mod tiers lit sa configuration ainsi depuis un script de bataille | **accessible** — la sonde ouvre le fichier de commande en lecture et distingue « absent » de « illisible » (essai n° 3) |
| Lua → Python (état, accusé) | `io.open(chemin, "a")` | **incertain** : le mod étudié n'écrit que depuis le frontend, jamais en bataille | **accessible** — `ECRITURE OK dans ./totalwar_ai/totalwar_ai_state.jsonl`, et `probe --status` côté Python a vu le fichier apparaître (essai n° 3) |
| Lua → Python (repli) | `out()` vers le journal du jeu | accessible | **accessible** — c'est ce canal qui a permis de diagnostiquer les trois essais |

**C'était le risque principal du prototype ; il est levé.** L'écriture depuis un
script de bataille fonctionne, dans le répertoire de travail du jeu, et Python
relit ce que le Lua écrit. Le repli par lecture du journal — plus lent et en
lecture seule — n'a donc pas à être retenu comme mécanisme principal.

Une précision qui compte pour la suite : ce droit d'écriture a été constaté
pendant la **phase de déploiement**, avant tout engagement. Rien ne dit encore
qu'il subsiste une fois la bataille commencée. Le droit est testé une seule fois
au démarrage et mémorisé — mais un refus ultérieur est désormais journalisé
(`ECRITURE REFUSEE dans …`) au lieu d'être avalé, et le journal du jeu continue
de recevoir chaque état en repli. Deux tests couvrent ce scénario.

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
| `ECRITURE INDISPONIBLE` | le droit d'écriture est refusé ; il a pourtant fonctionné à l'essai n° 3, donc vérifier d'abord que `<installation>/totalwar_ai/` existe |
| `ECRITURE REFUSEE dans …` | le droit a été perdu **en cours de bataille** alors qu'il était acquis au démarrage. Consigner le message et l'instant : ce serait une découverte, non couverte par les essais |
| `ERREUR dans publish_state : … (a nil value)` | la sonde emploie une fonction absente du bac à sable Lua du jeu. Relever le nom exact, l'ajouter à l'environnement amputé des tests, corriger |
| `multijoueur ou type de partie inconnu` | garde-fou volontaire ; vérifier qu'il s'agit bien d'une bataille solo |
| aucun état reçu côté Python | comparer le dossier affiché par `probe --status` et celui où le jeu écrit réellement |
| `uc:add_units a echoue` | l'unité est dans un groupe verrouillé ; en essayer une autre |

## Résultats de l'essai en bataille

### Essai n° 6 — 02/08/2026, 01 h 56

Le recensement automatique a répondu : voir le tableau d'observation ci-dessus,
dont il remplace toutes les lignes « inconnue » par des faits. Deux découvertes
structurantes — **moral et fatigue sont inaccessibles** — et deux défauts.

**Un ordre survivait à sa bataille.** Le journal montre, dès le démarrage :

```text
lecture OK : ./totalwar_ai/totalwar_ai_command.json existe deja
ACK {... "sequence":3, "status":"accepted" ...}
unite 1001 envoyee vers 326.934, -330.902
```

C'est la commande de la bataille **précédente**, restée sur le disque. La
mémoire anti-rejeu du Lua vit en mémoire et repart vide à chaque bataille : un
ordre d'hier s'appliquait donc à la partie d'aujourd'hui. La sonde neutralise
désormais toute commande trouvée au démarrage — elle en note la séquence comme
déjà traitée, sans supprimer le fichier, dont Python reste propriétaire.

**Un ordre accepté qui ne produit rien.** L'unité est restée à `x = 297.676`
pendant trente-trois secondes malgré un ordre acquitté vers `326.934`. Elle
n'était ni en déroute ni invalide. L'explication la plus probable est la phase :
tout cela se passe avant `Deployed`, et le moteur accepte alors les ordres sans
les exécuter. La sonde publie donc maintenant la phase dans chaque état, et le
CLI prévient quand un ordre ne peut pas aboutir. **À confirmer** : c'est une
hypothèse cohérente avec les relevés, pas une certitude.

**Ce que cet essai confirme par ailleurs.** La restitution du contrôle
fonctionne, et elle est tracée :

```text
controle rendu au joueur pour l'unite 1001
ACK {... "sequence":3, "status":"released", "note":"controle rendu apres delai" ...}
```

Cela corrige l'hypothèse notée à l'essai n° 5 : l'ordre ne « survivait » pas à
`release_control()`. L'unité n'avait tout simplement jamais bougé.

### Essai n° 5 — 02/08/2026, 01 h 34

Deux ordres de 150 m, exécutés : `26.9 → 176.9`, puis `176.9 → 326.9`. Les
séquences montent (`Ordre 2`, `Ordre 3`) : la reprise de numérotation tient en
jeu. Deux défauts nouveaux, tous deux côté Python.

**Le flux d'états se corrompait sous Windows.** Message relevé :

```text
ligne illisible dans totalwar_ai_state.jsonl (Expecting value: line 1 column 1)
  : game_time_ms":158100,"unit":{"id":"1001",...
```

Le fragment commence au milieu d'une ligne. Le Lua ouvre ses fichiers en mode
texte : sous Windows il écrit donc `\r\n`. Python lisait en mode texte, où la
paire devient un seul `\n`, et comptait les octets de la chaîne obtenue —
**un de moins que le fichier, par ligne**. L'offset dérivait d'un octet par
état publié ; après 157 états, la lecture reprenait 157 octets trop tôt, au
milieu d'une ligne. La lecture se fait désormais en binaire, avec de vrais
offsets d'octets. Un test rejoue 200 états en `\r\n` ; il échoue avec l'ancien
code en produisant le message exact ci-dessus.

**La mesure du déplacement annonçait `2.7 m` pour 150 m parcourus.** Elle
s'arrêtait au premier état dépassant le seuil. Elle attend maintenant que
l'unité soit immobile sur trois états consécutifs avant de conclure, et
distingue « arrivée » de « encore en mouvement ».

**Une observation à confirmer.** `release_after_ms` vaut 5 s, or l'unité a
parcouru ses 150 m — 30 m/s serait invraisemblable. L'ordre semblait donc
survivre à `release_control()`.

> **Corrigé par l'essai n° 6.** L'hypothèse était fausse. `release_control()`
> rend bien la main, et l'essai suivant montre une unité qui ne bouge pas du
> tout après un ordre acquitté. Le délai entre l'ordre et la lecture suivante
> était simplement bien plus long que les 5 s supposées : rien ne permettait de
> conclure. Un chiffre plausible n'est pas une mesure.

### Essai n° 4 — 01/08/2026, 22 h 42

**L'aller-retour complet est bouclé.**

```text
Unite 1001 (wh3_dlc20_chs_cha_daemon_prince_mnur) en (6.6, -330.9), controlable=True
Ordre 1 publie : deplacement de 20 m.
Accuse : accepted
```

Puis, à l'appel suivant, le jeu rapporte l'unité en `(26.9, -330.9)` : **20,3 m
parcourus**. Ce chiffre vient de `unit:position():get_x()`, donc du moteur, pas
d'une supposition.

| Critère du ticket | Résultat | Notes |
| --- | --- | --- |
| le script Lua est chargé par le jeu | **réussi** | |
| une unité réelle est détectée | **réussi** | `1001`, prince démon |
| son identifiant et sa position sont transmis à Python | **réussi** | flux d'états continu, 1 par seconde |
| une commande Python est lue par le Lua | **réussi** | |
| l'unité se déplace réellement | **réussi** | 20,3 m mesurés par le jeu |
| un accusé est reçu par Python | **réussi** | `accepted`, `deplacement lance` |
| la commande ne peut pas être exécutée deux fois | **réussi côté Lua** | et c'est ce qui a révélé le défaut ci-dessous |
| le joueur récupère le contrôle | **non confirmé** | le comportement est implémenté et testé hors jeu ; aucun relevé de cet essai ne l'atteste |
| l'arrêt d'urgence libère tout | **non confirmé** | idem |

**Le défaut, côté Python cette fois.** Trois `probe --move 20` successifs ont
tous publié `Ordre 1`. Le compteur de séquence vivait dans le processus : chaque
invocation du CLI repartait de 1. Le Lua a donc refusé les deux derniers ordres
— sa règle anti-rejeu a parfaitement fonctionné — mais Python, relisant le flux
d'accusés depuis le début, est retombé sur le **vieil** accusé du premier ordre,
de même numéro, et a affiché `accepted`. Le CLI annonçait un succès pour une
commande refusée, et l'unité ne bougeait qu'une fois sur trois.

Deux défauts distincts, deux corrections :

- `FileBridge.open()` **reprend** la numérotation là où le disque l'a laissée,
  en lisant la plus grande séquence présente dans le fichier de commande et
  dans le flux d'accusés ;
- `wait_for_ack` ne considère que les accusés écrits **après** la commande. Un
  accusé antérieur répond forcément à autre chose, même à numéro égal.

Les trois tests de non-régression ont été vérifiés défaillants avec l'ancien
code, où ils reproduisaient exactement le symptôme : un `ProbeAck(sequence=1,
status=ACCEPTED)` rendu pour une commande que le Lua avait rejetée.

**Une leçon d'ergonomie.** L'opérateur n'a pas vu l'unité bouger — vingt mètres
sur une carte de bataille, pour une figurine unique, ne se voient pas. Le CLI
mesure désormais le déplacement réellement constaté après chaque ordre, au lieu
de s'arrêter à l'accusé :

```text
Verification du deplacement...
Deplacement constate : 20.3 m.
```

Un ordre accepté mais sans effet — unité dans un groupe verrouillé, contrôle
rendu trop tôt — est donc signalé au lieu de passer pour un succès.

### Essai n° 3 — 01/08/2026, 22 h 10

Le diagnostic ajouté après l'essai n° 2 a fait exactement son travail : il a
répondu à la question de faisabilité, puis désigné le défaut suivant.

```text
[totalwar_ai] --- diagnostic des entrees-sorties ---
[totalwar_ai] io.open disponible
[totalwar_ai] ECRITURE OK dans ./totalwar_ai/totalwar_ai_state.jsonl
[totalwar_ai] le repertoire de travail du jeu contient donc bien ./totalwar_ai/
[totalwar_ai] lecture : ./totalwar_ai/totalwar_ai_command.json absent (normal avant la 1re commande)
[totalwar_ai] --- fin du diagnostic ---
[totalwar_ai] armee du joueur : 11 unites, 11 controlables, premiere = 1001
[totalwar_ai] ERREUR dans publish_state : …:96: attempt to perform arithmetic on field 'huge' (a nil value) (occurrence 1)
```

| Critère du ticket | Résultat | Notes |
| --- | --- | --- |
| le script Lua est chargé par le jeu | **réussi** | depuis `script/_lib/mod/` |
| l'écriture de fichier est possible en bataille | **réussi** | `ECRITURE OK` — c'était le risque principal du projet |
| la lecture de fichier est possible en bataille | **réussi** | fichier de commande ouvert, absence correctement distinguée d'une erreur |
| une unité réelle est détectée | **réussi** | 11 unités alliées, 11 contrôlables, première = `1001` |
| sa position est transmise à Python | **échec** | erreur de sérialisation, corrigée depuis |
| une commande Python est lue par Lua | **non testé** | bloqué par le point précédent |
| l'unité se déplace réellement | **non testé** | idem |
| un accusé est reçu par Python | **non testé** | idem |
| la commande ne peut pas être exécutée deux fois | **non testé** | idem |
| le joueur récupère le contrôle | **non testé** | idem |

**Ce que cet essai établit.** Le pont par fichiers est viable. Le Lua écrit dans
le répertoire de travail du jeu, et `probe --status` côté Python a vu le fichier
d'état apparaître : les deux moitiés se parlent bien par le même dossier. Sur
l'API d'observation, tout ce que la sonde appelle avant le point de crash a
répondu — `alliances`, `armies`, `units`, `count`, `item`, `is_controllable`,
`is_valid_target`, `unique_ui_id`, `type`, `position:get_x/y/z`. Les arguments
d'un appel Lua étant évalués avant l'appel, leur succès est acquis. Reste hors
d'atteinte de cet essai `bm:time_elapsed_ms()`, situé juste après.

**Le défaut.** `json_number` écartait NaN et l'infini en comparant à
`math.huge`, et arrondissait avec `math.floor`. Le bac à sable Lua du jeu ne
fournit ni l'un ni l'autre : `-math.huge` est une soustraction sur `nil`, donc
une erreur, à la toute première sérialisation. Le rappel périodique étant
protégé par `pcall` depuis l'essai n° 2, la sonde n'est pas morte — elle a
signalé l'erreur 160 fois d'affilée avec la bonne cadence. Le garde-fou a tenu ;
c'est le code qu'il protégeait qui était faux.

**La correction.** `json_number` n'emploie plus `math` du tout. NaN se reconnaît
à `value ~= value`, l'infini à `value * 0 ~= 0` (nul pour tout nombre fini), et
l'entier à `value % 1 == 0`. Deux tests de non-régression exécutent la sonde
dans un interpréteur où `math.huge` et `math.floor` ont été effacés ; vérifiés
défaillants avec l'ancien code, ils reproduisaient le message d'erreur exact
relevé en jeu.

**Leçon retenue.** Le bac à sable du jeu n'est pas la bibliothèque standard. La
seule défense fiable est d'exécuter le script contre un environnement amputé
avant chaque essai, plutôt que de supposer disponible ce qui l'est partout
ailleurs.

### Essai n° 2 — 01/08/2026, 21 h 45

Le script est placé aux deux emplacements dans le pack. Résultat :

```text
Loading mod file [script\_lib\mod\totalwar_ai_probe.lua]
        [totalwar_ai] === fichier charge (sonde v0.1.0) ===
        [totalwar_ai] contexte de bataille detecte : demarrage dans 1 seconde
Mod [script\_lib\mod\totalwar_ai_probe.lua] loaded successfully
Failed to load mod: [script\battle\mod\totalwar_ai_probe.lua]
...
[totalwar_ai] sonde active - protocole 0.1.0
[totalwar_ai] repertoire d'echange attendu : ./totalwar_ai/
```

| Critère du ticket | Résultat | Notes |
| --- | --- | --- |
| le script Lua est chargé par le jeu | **réussi** | depuis `script/_lib/mod/` |
| une unité réelle est détectée | **échec** | aucun message d'état, cause inconnue |
| sa position est transmise à Python | **échec** | les trois fichiers restent absents |
| une commande Python est lue par Lua | **non testé** | bloqué par le point précédent |
| l'unité se déplace réellement | **non testé** | idem |
| un accusé est reçu par Python | **non testé** | idem |
| la commande ne peut pas être exécutée deux fois | **non testé** | idem |
| le joueur récupère le contrôle | **non testé** | idem |

**Ce qui est acquis.** `PROBE:start()` s'est exécuté : `bm` existe,
`bm:is_multiplayer()` répond, `bm:repeat_callback()` est acceptée. La sonde est
donc bel et bien vivante dans une vraie bataille.

**`script/battle/mod/` : `Failed to load mod`.** Le second exemplaire, de même
nom de fichier, est refusé. Le plus probable est un conflit de nom avec
l'exemplaire déjà chargé, pas une invalidité de ce répertoire — un mod tiers y
place son script de bataille et fonctionne. À trancher un jour ; sans importance
pour l'instant, puisque `script/_lib/mod/` fonctionne.

**Pourquoi aucun état n'a été publié.** Le journal ne le dit pas, et c'est
précisément le problème : `publish_state` sortait en silence quand aucune unité
contrôlable n'était trouvée, et un rappel périodique qui lève une erreur meurt
sans laisser de trace. Trois causes restaient indistinguables : aucune unité
contrôlable pendant la phase de déploiement, une erreur d'API, ou un droit
d'écriture refusé.

**Corrections apportées.** La sonde ne se tait plus :

- un **diagnostic des entrées-sorties** est publié au démarrage, avant toute
  détection d'unité — il dit si `io.open` existe, si l'écriture passe, et
  distingue « dossier absent » de « droit refusé » ;
- l'**état de l'armée** est journalisé au démarrage (unités vues, contrôlables) ;
- `publish_state` **explique** son échec au lieu de sortir en silence, les trois
  premières fois puis toutes les vingt ;
- les rappels périodiques sont **protégés par `pcall`** et journalisent l'erreur.

Ces corrections sont désormais vérifiées automatiquement : le script est
**exécuté** contre un faux jeu dans `tests/integration/test_lua_probe_execution.py`
(23 tests). Ce harnais a d'ailleurs immédiatement attrapé un appel à une fonction
inexistante qui aurait fait échouer le troisième essai — et il couvre depuis le
bac à sable amputé découvert lors de ce même essai.

### Essai n° 1 — 01/08/2026, 21 h 14

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

**Établi en bataille réelle** (essais n° 3 et 4) : l'écriture et la lecture de
fichiers depuis un script de bataille, la détection des unités alliées, leur
identifiant, leur type, leur position, le fait qu'elles soient contrôlables —
et **l'aller-retour complet** : ordre publié par Python, lu et exécuté par le
Lua, déplacement mesuré par le jeu, accusé remonté à Python.

**Restent non confirmés en jeu** : la restitution du contrôle au joueur après
cinq secondes, et l'arrêt d'urgence. Les deux sont implémentés et couverts par
les tests d'exécution Lua, ce qui n'est pas la même chose que les avoir vus.

**Établi hors du jeu seulement** — vrai du code, muet sur le moteur :

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
  contre une reproduction en Python de la logique du script ;
- le script Lua lui-même est **exécuté** contre un faux jeu, y compris dans un
  environnement où `math` a été amputé comme dans le jeu.

Voir `tests/unit/test_file_bridge.py`, `tests/integration/test_lua_protocol.py`
et `tests/integration/test_lua_probe_execution.py`. Ces tests ne remplacent pas
l'essai en bataille et ne prétendent pas le faire : le faux jeu ne dit rien des
groupes verrouillés, ni de ce que le moteur fait vraiment d'un ordre.

## Décision

**Tranché par l'essai n° 3 :** le mécanisme de communication retenu est
**l'échange par fichiers**. Le repli par lecture du journal du jeu reste utile
comme canal de diagnostic — chaque état et chaque accusé y sont écrits en plus
du fichier — mais n'a pas à devenir le mécanisme principal.

Restent à trancher, à l'issue de l'aller-retour complet :

- les champs du protocole complet à rendre optionnels ;
- les actions à retirer du périmètre de la première intégration ;
- s'il faut faire évoluer `PROTOCOL_VERSION`.

Toute évolution du protocole doit être reportée dans
[`protocol.md`](protocol.md) avec un incrément de version.

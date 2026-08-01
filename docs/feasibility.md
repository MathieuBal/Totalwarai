# Faisabilité de l'intégration au jeu (Phase 0)

> **Statut : non commencé.** Ce document est un gabarit à remplir pendant le
> spike décrit dans le `README.md` (Ticket 003). Aucune ligne de ce dépôt ne
> suppose aujourd'hui que *Total War: WARHAMMER III* est installé, et aucune
> promesse de compatibilité ne doit être faite avant que ce document soit
> complété.

## Contexte

Le cœur Python (domaine, agent, sécurité, simulateur, mémoire) est terminé et
testable sans le jeu. Ce qui manque pour piloter une vraie bataille :

1. un mod Lua capable d'observer l'état d'une bataille de campagne ;
2. un moyen de communication entre ce mod et le processus Python ;
3. un traducteur des actions haut niveau du protocole vers des ordres réels.

## Environnement testé

À renseigner :

| Élément | Valeur observée |
| --- | --- |
| Version du jeu | |
| Système d'exploitation | |
| Bibliothèques de modding disponibles | |
| Emplacement des scripts de bataille | |
| Rechargement à chaud possible ? | |

## Observation — ce que le Lua expose réellement

Pour chaque donnée du schéma `UnitState` (voir `docs/protocol.md`), indiquer si
elle est accessible, sous quel nom, et à quel coût.

| Donnée | Accessible ? | API / méthode | Remarques |
| --- | --- | --- | --- |
| liste des unités alliées | | | |
| liste des unités ennemies visibles | | | |
| position (x, y, z) | | | |
| cap / orientation | | | |
| effectifs restants | | | |
| points de vie | | | |
| moral | | | |
| fatigue | | | |
| munitions | | | |
| unité engagée au corps à corps | | | |
| unité en déroute | | | |
| unité cachée | | | |
| cible courante | | | |
| identifiant stable de l'unité | | | |
| clé d'unité (pour la classification) | | | |

**Conclusion attendue :** la liste des champs du protocole qui devront être
marqués optionnels ou estimés côté Python.

## Commande — ce que le Lua permet d'ordonner

| Ordre | Possible ? | API / méthode | Latence observée |
| --- | --- | --- | --- |
| déplacer une unité vers un point | | | |
| déplacer un groupe en formation | | | |
| attaquer une unité désignée | | | |
| tirer sur une unité désignée | | | |
| rompre le combat | | | |
| tenir la position | | | |
| changer d'orientation | | | |
| rendre le contrôle au joueur | | | |

**Test minimal exigé par la Phase 0 :** observer au moins une unité et lui
envoyer un ordre de déplacement contrôlé, puis reprendre la main manuellement.

## Communication Lua ↔ Python

Options à évaluer dans cet ordre, en s'arrêtant à la première qui fonctionne :

1. mécanisme officiellement accessible depuis les scripts de bataille ;
2. échange par fichiers locaux en append-only ;
3. processus auxiliaire autorisé ;
4. automatisation externe, en dernier recours seulement.

| Option | Testée ? | Fonctionne ? | Fréquence soutenable | Remarques |
| --- | --- | --- | --- | --- |
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |

Contraintes à vérifier pour l'option retenue :

- ne bloque pas la boucle du jeu ;
- supporte un message tronqué sans perdre le flux ;
- permet au moins un aller-retour par seconde ;
- survit à une mise en pause et à un changement de bataille.

## Impossibilités constatées

À documenter explicitement, y compris les contournements envisagés et ceux
écartés. Une impossibilité clairement écrite vaut mieux qu'un contournement
fragile.

## Décision

À l'issue du spike, choisir et justifier :

- le mécanisme de communication ;
- les champs du protocole à rendre optionnels ;
- les actions à retirer du périmètre de la première intégration ;
- s'il faut ou non faire évoluer `PROTOCOL_VERSION`.

Toute évolution du protocole doit être reportée dans `docs/protocol.md` et
accompagnée d'un incrément de version.

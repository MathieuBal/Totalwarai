# 0003 — Adaptation bornée et explicable de la doctrine

- **Statut :** acceptée
- **Portée :** `totalwar_ai.learning.adaptation`, `totalwar_ai.agent.doctrine`

## Contexte

La Phase 4 du `README.md` demande un « comportement influencé par les batailles
précédentes ». La tentation naturelle est d'optimiser directement les
paramètres sur la récompense — descente de gradient, recherche aléatoire,
algorithme génétique.

## Décision

L'agent dérive de son historique un **profil de doctrine** : un petit ensemble
de réglages ajustés, chacun borné, chacun accompagné d'une phrase expliquant
pourquoi il a bougé.

Cinq réglages seulement sont ajustables (`ADJUSTABLES`) : rayon de menace pour
les tireurs, distance d'engagement, taille de la réserve, seuil de poursuite,
espacement de la ligne. Le profil est stocké en JSON, par composition d'armée.

## Raisons

- Le `README.md` place l'explicabilité avant la complexité. Un réglage qui
  bouge sans qu'on sache pourquoi est un réglage qu'on ne saura pas déboguer
  quand l'agent se mettra à mal jouer.
- Avec quelques batailles par session, une optimisation numérique sur-apprend
  le bruit. Des règles lisibles tirées de statistiques agrégées sont plus
  honnêtes à ce volume de données.
- Les bornes garantissent qu'un historique aberrant — ou corrompu — ne peut pas
  produire une doctrine absurde. Le test `test_ajustements_toujours_dans_les_bornes`
  applique dix cycles d'adaptation d'affilée et vérifie que rien ne dérive.

## Ce que l'apprentissage n'a pas le droit de faire

Aucun garde-fou de `safety_rules` n'est ajustable : l'interdiction de charger
avec l'artillerie, la protection du seigneur, le seuil de charge suicidaire et
la limite d'ordres par minute sont hors de portée. Le seul réglage de sécurité
touché est le rayon de menace des tireurs, et **uniquement à la hausse** —
l'apprentissage peut rendre l'agent plus prudent, jamais moins.

## Conséquences

- Une bataille jouée avec mémoire n'est plus reproductible à partir de la seule
  graine : il faut aussi l'historique. Le rapport enregistre donc le profil
  appliqué, et `--no-adapt` permet de revenir au comportement de référence.
- Le déterminisme reste garanti sans dépôt de mémoire : c'est ce que vérifient
  les tests de scénario.
- Quand le volume de batailles deviendra suffisant (Phase 6), une optimisation
  numérique pourra remplacer ces règles — en gardant les bornes et
  l'enregistrement des raisons, qui restent utiles quel que soit le mécanisme.

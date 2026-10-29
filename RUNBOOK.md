# FusionFlow AI — Runbook opérationnel

## 1. Déclencheurs
- Réponse d'agent incorrecte ou non justifiée.
- Violation d'une règle de gouvernance ou appel d'outil inattendu.
- Échec des tests CI, erreur de déploiement ou dégradation des KPI.

## 2. Contention
1. Ouvrir la Dashboard et activer **Kill Switch**.
2. Confirmer que les nouveaux appels Fusion et les actions Pull/Push sont bloqués.
3. Ne pas déployer une version qui a échoué aux tests.
4. Informer le responsable technique si l'impact est important.

## 3. Diagnostic
1. Noter l'identifiant d'incident, la version et la méthode SemVer sélectionnée.
2. Consulter le journal d'activité, les logs API et le dernier run GitHub Actions.
3. Distinguer les faits observés des hypothèses; ne jamais inventer un résultat de test.
4. Examiner les changements récents et le dernier tag Git stable.

## 4. Rétablissement / rollback
1. Identifier la dernière version stable et vérifier son tag SemVer.
2. Production : *GitHub > Actions > FusionFlow CD > Run workflow* avec le tag stable (ex. `v1.0.0`) — l'image de ce tag est redéployée après approbation. Code local : bouton Rollback Git de la Dashboard (crée une branche de sauvegarde).
3. Faire valider le rollback par une personne responsable avant tout changement de production.
4. Rejouer les tests (`pytest`) et vérifier `/api/health` : le champ `version` doit être celle attendue.
5. Documenter la version restaurée, les résultats et les risques résiduels.

## 5. Réactivation
1. Vérifier que le service répond et que les tests critiques passent.
2. Réactiver le workflow uniquement après validation humaine.
3. Désactiver le Kill Switch depuis la Dashboard.
4. Surveiller les métriques et conserver les traces de l'incident.

## 6. Limites actuelles
- Le Kill Switch est stocké en mémoire et revient à la valeur `.env` au redémarrage.
- Le déploiement CD n'a lieu que si les secrets `DEPLOY_*` sont configurés et que l'environnement `production` a des relecteurs obligatoires.
- Le rollback logique (version affichée) et le rollback de production (redéploiement d'un tag) sont deux actions distinctes : faites les deux.
- Le Push envoie uniquement des commits déjà créés; il ne crée pas automatiquement de commit.
- L'API locale n'a pas d'authentification intégrée. Ne l'exposez pas publiquement sans ajouter une authentification et des contrôles d'accès.

# FusionFlow AI — Incident Operations, Governance & DevOps

Dashboard légère (thème clair) construite avec **FastAPI + LangGraph**. LangGraph orchestre le workflow multi-agents : détection, diagnostic, gouvernance, correction conditionnelle et reporting. Aucun webhook Fusion Agentic n’est utilisé. La dashboard affiche aussi les Agent Cards, le dépôt Git local et les runs GitHub Actions.

## Fonctionnalités ajoutées
- **Agent Cards** : rôle, propriétaire, outils autorisés, limites, KPI et version SemVer.
- **SemVer** : Major (`x.0.0`) pour une rupture/refonte, Minor (`x.y.0`) pour une capacité compatible, Patch (`x.y.z`) pour un correctif.
- **GitHub** : état du dépôt local, branche, remote, fichiers modifiés, actions Pull/Push et création/envoi de tags SemVer (désactivées par défaut).
- **CI** : `.github/workflows/ci.yml` lance ruff, la compilation, les tests pytest, un smoke test (l'app démarre) et un `docker build` sur push et pull request.
- **CD** : `.github/workflows/cd.yml` — sur tag `vX.Y.Z` : tests, build + push de l'image Docker sur GHCR, puis déploiement SSH avec **approbation manuelle** (voir section « CI/CD et cycle de release »).
- **Kill Switch** : suspend les actions Git Pull/Push.
- **Runbook** : `RUNBOOK.md` décrit contention, diagnostic, rollback et réactivation.
- **Docker** : `Dockerfile` et `docker-compose.yml` pour exécuter le service dans un conteneur.

## Important
- Le traitement des incidents est désormais exécuté par LangGraph; aucun export ni service Fusion Agentic n’est nécessaire à l’exécution.
- Ne publiez jamais `.env` ni une clé GitHub.
- Sans `OPENROUTER_API_KEY`, l'application utilise une analyse heuristique de secours, uniquement utile pour tester l'interface.
- Les cartes affichent les agents définis dans ce starter; adaptez `AGENT_CARDS` dans `main.py` et `agent_cards.json` aux agents exacts de votre workflow exporté.
- L'API n'a pas encore d'authentification intégrée. Utilisez-la en local seulement tant que l'authentification et les contrôles d'accès ne sont pas ajoutés.

## Installation Windows
Ouvrez PowerShell dans ce dossier :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Linux / macOS :

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configurez `.env` :
- `OPENROUTER_API_KEY` : clé LLM (facultative pour l'interface).
- `AGENT_VERSION=1.0.0` : version affichée dans les Agent Cards.
- `GITHUB_REPOSITORY=owner/repo` : dépôt GitHub pour afficher le dernier run CI. Pour un dépôt privé, renseignez `GITHUB_TOKEN` avec un token à permissions minimales.
- `GIT_SYNC_ENABLED=false` : laissez désactivé au début. Activez `true` seulement après avoir vérifié le dépôt local, le remote `origin` et l'authentification Git.
- `KILL_SWITCH_ACTIVE=false` : état initial du coupe-circuit.
- `AUTO_TAG_ON_VERSION=false` : mettez `true` (avec `GIT_SYNC_ENABLED=true`) pour que la création d'une version crée aussi le tag Git `vX.Y.Z`.

Lancez :

```powershell
uvicorn main:app --reload
```

Ouvrez http://127.0.0.1:8000. Documentation API : http://127.0.0.1:8000/docs.

## GitHub Actions
1. Envoyez le projet dans votre repository GitHub (sans `.env`).
2. Le workflow `FusionFlow CI` s'exécute sur `push` et `pull_request`.
3. Consultez l'onglet **Actions** du repository.
4. Le CD ne déploie rien tant que vous n'avez pas choisi/configuré un hébergeur et une étape de déploiement avec approbation.

## Git Pull / Push depuis la Dashboard
Les boutons sont désactivés par défaut. Avant de les activer, assurez-vous que le dossier est un dépôt Git, que `origin` pointe vers le bon repository et que les identifiants sont configurés. Pull utilise `git pull --ff-only` et refuse les changements locaux non commités. Push envoie les commits existants sur la branche active; il **ne crée pas** de commit automatiquement.

## Docker
Après avoir créé `.env` :

```powershell
docker compose up --build
```

## Tests et limites
- Tests automatisés : `pip install -r requirements-dev.txt` puis `pytest` (auth, rôles, workflow LangGraph, SemVer MAJOR/MINOR/PATCH, rollback, Kill Switch, cycle Git réel sur dépôt temporaire). Lint : `ruff check .`.
- Vérification de syntaxe : `python -m compileall -q main.py`.
- Kill Switch en mémoire : il revient à la valeur de `.env` au redémarrage.
- Aucun rollback automatique de production n'est lancé; suivez `RUNBOOK.md` et exigez une approbation humaine.
- Aucun résultat de test, push, pull ou déploiement n'est inventé par la Dashboard : les statuts affichés dépendent des commandes/API réellement disponibles.


## Comptes Admin / User, historique et versionnement

1. Copiez `.env.example` vers `.env` et remplacez **ADMIN_PASSWORD** par un mot de passe privé d'au moins 10 caractères avant de démarrer l'application.
2. Le compte administrateur est créé au premier lancement avec `ADMIN_EMAIL` et `ADMIN_PASSWORD`. Si la base `fusionflow.db` existait déjà avant la configuration, supprimez-la uniquement si vous acceptez de perdre les historiques de cette base, puis redémarrez pour créer le compte admin.
3. Les utilisateurs créent leur propre compte depuis l'écran de connexion. Chaque utilisateur ne voit que son historique d'analyses; l'accès aux opérations GitHub, au Kill Switch, aux versions et aux métriques globales est réservé à l'admin.
4. L'administrateur peut créer une version **PATCH**, **MINOR** ou **MAJOR**, voir l'historique des versions, les comptes, les analyses et les compteurs de tokens. Les compteurs sont fournis par le fournisseur quand disponibles; sinon ils sont estimés à partir du texte. Le coût monétaire n'est pas calculé sans tarification exacte du modèle.
5. Le bouton **Rollback version logique** remet l'étiquette de version active à la précédente (il peut être répété pour reculer de plusieurs versions), mais **ne restaure pas le code Git**. Pour restaurer le code, l'admin doit aussi choisir le commit/tag stable et effectuer le rollback Git après vérification et sauvegarde.
6. Git Pull/Push restent désactivés par défaut (`GIT_SYNC_ENABLED=false`). Activez-les uniquement sur un poste de confiance et après configuration du dépôt. Le CI GitHub Actions exécute des vérifications; le CD de déploiement dépend encore de l'hébergeur.

La connexion est prévue pour un environnement local/de démonstration. Avant une mise en production publique, ajoutez HTTPS, `COOKIE_SECURE=true`, protection CSRF/rate limiting et un vrai mécanisme de déploiement/rollback adapté à l'hébergeur.

## Espaces Admin et User

- **Admin** : voit les métriques globales, le journal d'activité de tous les utilisateurs, les 5 Agent Cards et leurs états, les tokens, la gouvernance, les versions SemVer, le rollback, GitHub et CI/CD. Les endpoints admin sont protégés côté serveur par le rôle `admin`.
- **User** : voit uniquement son espace chatbot LangGraph et ses propres analyses. Il ne voit pas les métriques globales, les Agent Cards, le journal global, la gouvernance ni les outils GitHub/versionnement.
- **Versionnement** : `PATCH` corrige, `MINOR` ajoute une capacité compatible, `MAJOR` marque une rupture importante. Le rollback logique ne restaure pas le code ; le rollback Git est séparé et crée une branche de sauvegarde.
- **Monitoring** : tokens et temps moyen sont calculés depuis les analyses stockées. Si le fournisseur ne fournit pas l'usage réel, les tokens peuvent être estimés.

Configurez `ADMIN_EMAIL` et un mot de passe fort dans `.env` avant le premier démarrage pour créer le compte administrateur. Les nouveaux comptes créés depuis l'écran d'inscription ont toujours le rôle `user`.


## CI/CD et cycle de release

```
Dashboard : créer version MINOR  ->  tag Git v1.1.0 (auto si AUTO_TAG_ON_VERSION=true, sinon bouton « Créer tag »)
        ->  « Push tag »  ->  GitHub Actions « FusionFlow CD »
        ->  tests  ->  image Docker ghcr.io/<owner>/<repo>:1.1.0  ->  approbation manuelle  ->  déploiement
        ->  contrôle /api/health (la version déployée doit être 1.1.0)
```

**Prérequis GitHub**
1. Le dépôt doit avoir **ce dossier comme racine** (`.github/workflows/` à la racine du repo).
2. *Settings > Environments > New environment* `production` > **Required reviewers** : c'est l'approbation manuelle avant le déploiement.
3. Pour le déploiement SSH, ajoutez les *secrets* `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` (et optionnellement `DEPLOY_PATH`, défaut `/opt/fusionflow`) et la *variable* `DEPLOY_URL` (ex. `https://fusionflow.exemple.com`). Sur le serveur : Docker + Docker Compose, et un fichier `.env` dans `DEPLOY_PATH`.
4. Sans ces secrets, le CD publie l'image sur GHCR mais **n'effectue aucun déploiement** et le signale par un avertissement.

**Rollback** : *Actions > FusionFlow CD > Run workflow* avec le tag stable précédent (ex. `v1.0.0`) redéploie cette image. Voir `RUNBOOK.md`.

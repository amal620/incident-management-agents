const $ = (id) => document.getElementById(id);
const form = $("incident-form");
const messageInput = $("incident-message");
const runButton = $("run-button");
const formError = $("form-error");
let historyRows = [];
let totalDuration = 0;
let currentUser = null;
let authRegisterMode = false;

const modeLabels = { major: "Major", minor: "Minor", patch: "Patch" };
const now = new Date();
$("current-date").textContent = new Intl.DateTimeFormat("fr-FR", {
  weekday: "short", day: "2-digit", month: "short", year: "numeric"
}).format(now);

messageInput.addEventListener("input", () => {
  $("char-count").textContent = `${messageInput.value.length} / 8000`;
});

function safeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

async function refreshHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("health");
    const health = await response.json();
    $("connection-label").textContent = health.llm_configured ? "LLM connecté" : "Mode secours actif";
    $("metric-fusion").textContent = "LangGraph";
    $("fusion-foot").textContent = "Orchestration locale uniquement";
  } catch {
    $("connection-label").textContent = "API indisponible";
  }
}
$("refresh-button").addEventListener("click", refreshHealth);
refreshHealth();

function setAgent(id, state, label, duration = "—") {
  const row = $(id);
  if (!row) return;
  const stateEl = row.querySelector(".agent-state");
  stateEl.className = "agent-state " + state;
  stateEl.querySelector("span").textContent = label;
  row.querySelector(".agent-duration").textContent = duration;
}

function resetAgents() {
  setAgent("agent-analyzer", "running", "En cours…");
  setAgent("agent-orchestrator", "running", "Coordination…");
  setAgent("agent-fusion", "", "En attente");
  setAgent("agent-correction", "", "En attente");
  setAgent("agent-reporting", "", "En attente");
}
function setRunning(running) {
  runButton.disabled = running;
  runButton.querySelector("span:first-child").textContent = running ? "Analyse en cours…" : "Analyser l'incident";
  runButton.querySelector(".button-arrow").textContent = running ? "…" : "→";
}
function statusPill(status) {
  const el = $("fusion-status-pill");
  el.className = "status-pill";
  const labels = {
    accepted: ["ok", "SOLUTION VALIDÉE"],
    not_configured: ["pending", "NON CONFIGURÉ"],
    error: ["error", "ERREUR DE GOUVERNANCE"],
    unknown: ["pending", "INCONNU"],
    blocked_kill_switch: ["error", "BLOQUÉ — KILL SWITCH"]
  };
  const [cls, label] = labels[status] || labels.unknown;
  el.classList.add(cls);
  el.textContent = label;
}
function renderResult(data) {
  $("empty-state").hidden = true;
  $("result-content").hidden = false;
  $("result-id").textContent = data.incident_id;
  $("result-severity").textContent = data.severity || "À confirmer";
  $("result-severity").style.color = ({High:"#c85c65",Medium:"#c68a29",Low:"#239a73"})[data.severity] || "#c68a29";
  $("result-mode").textContent = modeLabels[data.mode] || data.mode;
  $("result-analysis").textContent = data.analysis || "Aucune analyse reçue.";
  $("result-duration").textContent = `${data.duration_ms ?? "—"} ms`;
  const steps = $("recommended-steps");
  steps.replaceChildren();
  (data.recommended_steps || []).forEach((step) => {
    const li = document.createElement("li");
    li.textContent = step;
    steps.appendChild(li);
  });
  $("step-count").textContent = `${(data.recommended_steps || []).length} étapes`;
  const response = data.final_report || "Aucun rapport généré.";
  $("fusion-response-text").textContent = response;
  statusPill(data.governance_status === "VALID" ? "accepted" : data.governance_status === "INVALID" ? "error" : "not_configured");
  $("warning-text").hidden = !data.warning;
  $("warning-text").textContent = data.warning || "";
  $("prompt-tokens").textContent = Number(data.prompt_tokens || 0).toLocaleString("fr-FR");
  $("completion-tokens").textContent = Number(data.completion_tokens || 0).toLocaleString("fr-FR");
  $("total-tokens").textContent = Number(data.total_tokens || 0).toLocaleString("fr-FR");
  $("response-style-result").textContent = ({brief:"Courte",normal:"Normale",detailed:"Détaillée"})[data.response_style] || "Détaillée";
  $("metric-total").textContent = String(historyRows.length).padStart(2, "0");
  totalDuration += Number(data.duration_ms || 0);
  $("metric-time").innerHTML = `${Math.round(totalDuration / Math.max(historyRows.length, 1))} <span class="metric-unit">ms</span>`;
}
function addHistory(data) {
  historyRows.unshift(data);
  const tbody = $("activity-body");
  tbody.replaceChildren();
  historyRows.slice(0, 8).forEach((item) => {
    const tr = document.createElement("tr");
    const message = String(item.message || "");
    const description = message.length > 54 ? message.slice(0, 54) + "…" : message;
    const statusClass = item.governance_status === "VALID" ? "" : item.governance_status === "INVALID" ? "error" : "pending";
    const statusText = item.governance_status === "VALID" ? "Validé" : item.governance_status === "INVALID" ? "À revoir" : "Local";
    tr.innerHTML = `<td>${safeText(item.incident_id)}</td>
      <td title="${safeText(message)}">${safeText(description)}</td>
      <td><span class="table-mode">${safeText(modeLabels[item.mode] || item.mode)}</span></td>
      <td><span class="table-status ${statusClass}">${safeText(statusText)}</span></td>
      <td>${Number(item.duration_ms || 0)} ms</td>`;
    tbody.appendChild(tr);
  });
  if (!historyRows.length) tbody.innerHTML = '<tr class="table-empty"><td colspan="5">Aucune activité pour le moment.</td></tr>';
}
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  formError.hidden = true;
  const message = messageInput.value.trim();
  const mode = document.querySelector('input[name="mode"]:checked')?.value || "minor";
  const response_style = $("response-style").value || "detailed";
  if (message.length < 5) {
    formError.textContent = "Décrivez l'incident avec au moins 5 caractères.";
    formError.hidden = false;
    return;
  }
  setRunning(true);
  resetAgents();
  $("result-id").textContent = "Traitement en cours…";
  try {
    const response = await fetch("/api/incidents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, mode, response_style })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "La requête n'a pas abouti.");
    setAgent("agent-analyzer", "done", "Terminé", `${data.duration_ms} ms`);
    setAgent("agent-orchestrator", "done", "Terminé", "LangGraph");
    setAgent("agent-fusion", data.governance_status === "VALID" ? "done" : "failed", `Gouvernance ${data.governance_status || "inconnue"}`, `${data.correction_attempts || 0} correction(s)`);
    const traceByName = Object.fromEntries((data.agent_trace || []).map(item => [item.agent, item]));
    const traceMapping = [["agent-correction", "Agent de Correction"], ["agent-reporting", "Agent de Reporting"]];
    for (const [elementId, name] of traceMapping) {
      const item = traceByName[name];
      if (item) setAgent(elementId, item.status === "SUCCESS" ? "done" : item.status === "INVALID" ? "failed" : "", item.status || "Terminé", item.detail || "LangGraph");
      else setAgent(elementId, "", "Non nécessaire", "0 exécution");
    }
    addHistory(data);
    renderResult(data);
  } catch (error) {
    setAgent("agent-analyzer", "failed", "Erreur");
    setAgent("agent-orchestrator", "failed", "Arrêté");
    formError.textContent = error.message || "Impossible de joindre le serveur.";
    formError.hidden = false;
  } finally {
    setRunning(false);
  }
});
$("clear-log").addEventListener("click", () => {
  historyRows = [];
  totalDuration = 0;
  $("metric-total").textContent = "00";
  $("metric-time").innerHTML = '— <span class="metric-unit">ms</span>';
  $("activity-body").innerHTML = '<tr class="table-empty"><td colspan="5">Aucune activité pour le moment.</td></tr>';
  historyRows = [];
});

// Operations panel: real local Git state, latest GitHub Actions run and governance cards.
let killSwitchActive = false;
let gitSyncEnabled = false;
function setMiniStatus(element, state, label) {
  if (!element) return;
  element.className = `mini-status ${state || "pending"}`;
  element.textContent = label;
}
function renderAgentCards(agents) {
  const grid = $("agent-cards-grid");
  grid.replaceChildren();
  (agents || []).forEach((agent) => {
    const card = document.createElement("article");
    card.className = "agent-card";
    card.innerHTML = `
      <div class="agent-card-head"><strong>${safeText(agent.name)}</strong><span class="agent-card-id">${safeText(agent.id)}</span></div>
      <p>${safeText(agent.role)}</p>
      <dl>
        <dt>Propriétaire</dt><dd>${safeText(agent.owner || "Non défini")}</dd>
        <dt>Outils</dt><dd>${safeText((agent.tools || []).join(", "))}</dd>
        <dt>Limites</dt><dd>${safeText(agent.limits || "Non documentées")}</dd>
      </dl>
      <div class="agent-card-foot"><span>KPI : ${safeText(agent.kpi || "À définir")}</span><span>v${safeText(agent.version || "1.0.0")}</span></div>`;
    grid.appendChild(card);
  });
}
async function refreshOps() {
  const button = $("refresh-ops");
  if (button) button.disabled = true;
  try {
    const response = await fetch("/api/ops/status");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Impossible de lire le statut opérations.");
    const git = data.git || {};
    $("git-branch").textContent = git.available ? (git.branch || "Branche inconnue") : "Dépôt Git non détecté";
    $("git-remote").textContent = git.remote || "Configurez un remote nommé origin dans le dépôt local.";
    $("git-changes").textContent = git.available ? `${git.changed_files} fichier(s) modifié(s)` : "Git indisponible";
    setMiniStatus($("git-clean"), git.clean ? "ok" : git.available ? "pending" : "error", git.clean ? "Propre" : git.available ? "Modifications" : "Non connecté");
    gitSyncEnabled = Boolean(git.sync_enabled);
    $("pull-button").disabled = !git.available || !gitSyncEnabled || killSwitchActive;
    $("push-button").disabled = !git.available || !gitSyncEnabled || killSwitchActive;
    const gh = data.github || {};
    $("ci-status").textContent = gh.configured ? (gh.workflow_status || "Statut inconnu") : "Repository non configuré";
    $("ci-description").textContent = gh.configured ? `Repository : ${gh.repository}` : "Ajoutez GITHUB_REPOSITORY=owner/repo dans .env pour lire le dernier run.";
    if (gh.workflow_url) {
      $("ci-link").href = gh.workflow_url;
      $("ci-link").hidden = false;
    } else $("ci-link").hidden = true;
    $("version-chip").textContent = `v${data.version || "1.0.0"}`;
    $("version-current").textContent = `v${data.version || "1.0.0"}`;
    $("tag-button").textContent = `Créer tag v${data.version || "1.0.0"}`;
    $("tag-button").disabled = !git.available || !git.sync_enabled || Boolean(data.safety?.kill_switch_active);
    $("push-tag-button").disabled = !git.available || !git.sync_enabled || Boolean(data.safety?.kill_switch_active);
    killSwitchActive = Boolean(data.safety?.kill_switch_active);
    updateKillSwitchUI();
    renderAgentCards(data.agents || []);
    $("ops-message").textContent = `Git sync : ${git.sync_enabled ? "activé par configuration" : "désactivé par défaut"}. CI : ${gh.workflow_status || "non vérifié"}. Le CD réel doit être relié à votre hébergeur; aucun déploiement n'est simulé.`;
  } catch (error) {
    $("ops-message").textContent = error.message || "Erreur lors de l'actualisation GitHub/CI/CD.";
  } finally {
    if (button) button.disabled = false;
  }
}
function updateKillSwitchUI() {
  $("kill-state").className = `mini-status ${killSwitchActive ? "error" : "ok"}`;
  $("kill-state").textContent = killSwitchActive ? "ACTIF" : "Désactivé";
  $("kill-switch-button").textContent = killSwitchActive ? "Désactiver le Kill Switch" : "Activer le Kill Switch";
  $("pull-button").disabled = killSwitchActive || !gitSyncEnabled || $("git-clean").textContent === "Non connecté";
  $("push-button").disabled = killSwitchActive || !gitSyncEnabled || $("git-clean").textContent === "Non connecté";
  if ($("tag-button")) $("tag-button").disabled = killSwitchActive || !gitSyncEnabled || $("git-clean").textContent === "Non connecté";
  if ($("push-tag-button")) $("push-tag-button").disabled = killSwitchActive || !gitSyncEnabled || $("git-clean").textContent === "Non connecté";
}
async function gitAction(action) {
  const humanAction = action.toUpperCase();
  if (!window.confirm(`Confirmer ${humanAction} depuis le dépôt local ?`)) return;
  $("ops-message").textContent = `${humanAction} en cours…`;
  $("pull-button").disabled = true;
  $("push-button").disabled = true;
  try {
    const response = await fetch(`/api/git/${action}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `${humanAction} a échoué.`);
    $("ops-message").textContent = data.message || `${humanAction} terminé.`;
    await refreshOps();
  } catch (error) {
    $("ops-message").textContent = error.message || `${humanAction} a échoué.`;
    await refreshOps();
  }
}
$("refresh-ops").addEventListener("click", refreshOps);
$("pull-button").addEventListener("click", () => gitAction("pull"));
$("push-button").addEventListener("click", () => gitAction("push"));
async function tagAction(action) {
  const label = action === "tag" ? "créer un tag SemVer local" : "envoyer le tag SemVer à GitHub";
  if (!window.confirm(`Confirmer : ${label} ? La version provient de AGENT_VERSION dans .env.`)) return;
  try {
    const response = await fetch(`/api/git/${action === "tag" ? "tag" : "push-tag"}`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Action de versionnement échouée.");
    $("ops-message").textContent = data.message;
  } catch (error) {
    $("ops-message").textContent = error.message || "Action de versionnement échouée.";
  }
}
$("tag-button").addEventListener("click", () => tagAction("tag"));
$("push-tag-button").addEventListener("click", () => tagAction("push-tag"));
$("kill-switch-button").addEventListener("click", async () => {
  const next = !killSwitchActive;
  const prompt = next
    ? "Activer le Kill Switch ? Les nouveaux appels Fusion et les actions Git seront suspendus jusqu'à désactivation."
    : "Désactiver le Kill Switch et réautoriser les actions configurées ?";
  if (!window.confirm(prompt)) return;
  try {
    const response = await fetch("/api/ops/kill-switch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Modification refusée.");
    killSwitchActive = Boolean(data.active);
    updateKillSwitchUI();
    $("ops-message").textContent = data.message;
    await refreshOps();
  } catch (error) {
    $("ops-message").textContent = error.message || "Impossible de modifier le Kill Switch.";
  }
});
bootstrapAuth();


// Authentication and strict role separation: the existing dashboard layout remains unchanged.
function showAuthError(message) {
  const el = $("auth-error"); el.textContent = message; el.hidden = false;
}
function configureAuthForm() {
  $("auth-title").textContent = authRegisterMode ? "Créer votre compte" : "Bienvenue";
  $("auth-description").textContent = authRegisterMode ? "Créez un espace utilisateur pour suivre vos incidents." : "Connectez-vous à votre espace FusionFlow AI.";
  $("auth-submit").textContent = authRegisterMode ? "Créer le compte →" : "Se connecter →";
  $("auth-toggle").textContent = authRegisterMode ? "J'ai déjà un compte" : "Créer un compte utilisateur";
  $("auth-name").hidden = !authRegisterMode;
  $("auth-name-label").hidden = !authRegisterMode;
  $("auth-password").autocomplete = authRegisterMode ? "new-password" : "current-password";
  $("auth-error").hidden = true;
}
$("auth-toggle").addEventListener("click", () => { authRegisterMode = !authRegisterMode; configureAuthForm(); });
$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("auth-error").hidden = true;
  const payload = {email: $("auth-email").value.trim(), password: $("auth-password").value};
  if (authRegisterMode) payload.display_name = $("auth-name").value.trim();
  try {
    const response = await fetch(authRegisterMode ? "/api/auth/register" : "/api/auth/login", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data = await response.json(); if (!response.ok) throw new Error(data.detail || "Connexion impossible.");
    await enterDashboard(data);
  } catch (error) { showAuthError(error.message || "Connexion impossible."); }
});
async function enterDashboard(user) {
  currentUser = user;
  document.body.classList.toggle("role-admin", user.role === "admin");
  document.body.classList.toggle("role-user", user.role !== "admin");
  $("auth-overlay").hidden = true;
  $("profile-name").textContent = user.display_name || user.email;
  $("profile-role").textContent = user.role === "admin" ? "Administrateur" : "Utilisateur";
  $("top-avatar").textContent = (user.display_name || user.email || "U").slice(0,1).toUpperCase();
  $("connection-label").textContent = user.role === "admin" ? "Session admin active" : "Espace utilisateur actif";
  if (user.role === "admin") {
    $("overview-nav").textContent = "Vue d'ensemble";
    $("workspace-breadcrumb").textContent = "Espace Administrateur";
    $("workspace-title").innerHTML = 'Espace <span>Administrateur.</span>';
    $("workspace-subtitle").textContent = "Gérez les incidents, les Agent Cards, la gouvernance, les versions et le cycle GitHub/CI-CD.";
    $("incident-kicker").textContent = "LANGGRAPH ADMIN CHAT";
    $("incident-heading").textContent = "Assistant administrateur";
    $("incident-description").textContent = "Décrivez un incident ou demandez une analyse détaillée avant de gérer une version.";
    $("run-button").querySelector("span:first-child").textContent = "Analyser avec LangGraph";
    await refreshOps(); await refreshAdminPanels();
  } else {
    $("overview-nav").textContent = "Mon espace";
    $("workspace-breadcrumb").textContent = "Espace Utilisateur";
    $("workspace-title").innerHTML = 'Mon espace <span>assistant.</span>';
    $("workspace-subtitle").textContent = "Décrivez votre problème et échangez avec le chatbot LangGraph. Votre historique reste privé.";
    $("incident-kicker").textContent = "VOTRE CHATBOT LANGGRAPH";
    $("incident-heading").textContent = "Comment puis-je vous aider ?";
    $("incident-description").textContent = "Expliquez votre problème et choisissez une réponse courte, normale ou détaillée.";
    $("run-button").querySelector("span:first-child").textContent = "Envoyer au chatbot";
    await loadMyHistory();
  }
}
async function bootstrapAuth() {
  configureAuthForm();
  try {
    const response = await fetch("/api/auth/me");
    if (!response.ok) throw new Error("not-authenticated");
    await enterDashboard(await response.json());
  } catch {
    $("auth-overlay").hidden = false;
  }
}
$("logout-button").addEventListener("click", async () => {
  try { await fetch("/api/auth/logout", {method:"POST"}); } catch {}
  currentUser = null; document.body.classList.remove("role-admin","role-user");
  $("auth-email").value = ""; $("auth-password").value = ""; $("auth-overlay").hidden = false;
  authRegisterMode = false; configureAuthForm();
});
async function loadMyHistory() {
  try {
    const response = await fetch("/api/incidents/mine"); if (!response.ok) return;
    const data = await response.json();
    historyRows = (data.items || []).map(item => ({...item, final_report: item.analysis || ""}));
    const tbody = $("activity-body"); tbody.replaceChildren();
    historyRows.slice(0,8).forEach(item => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${safeText(item.incident_id)}</td><td title="${safeText(item.message)}">${safeText(String(item.message).slice(0,54))}</td><td><span class="table-mode">${safeText(modeLabels[item.mode] || item.mode)}</span></td><td><span class="table-status ${item.governance_status === "VALID" ? "" : "pending"}">${safeText(item.governance_status || "Local")}</span></td><td>${Number(item.duration_ms || 0)} ms</td>`;
      tbody.appendChild(tr);
    });
    if (!historyRows.length) tbody.innerHTML = '<tr class="table-empty"><td colspan="5">Aucune activité pour le moment.</td></tr>';
    $("metric-total").textContent = String(historyRows.length).padStart(2,"0");
  } catch {}
}
async function refreshAdminPanels() {
  try {
    const [metricsResponse, usersResponse, versionsResponse, incidentsResponse] = await Promise.all([
      fetch("/api/admin/metrics"), fetch("/api/admin/users"), fetch("/api/admin/versions"), fetch("/api/admin/incidents")
    ]);
    for (const r of [metricsResponse, usersResponse, versionsResponse, incidentsResponse]) if (!r.ok) throw new Error("Impossible de charger les outils admin.");
    const metrics = await metricsResponse.json(), users = await usersResponse.json(), versions = await versionsResponse.json(), incidents = await incidentsResponse.json();
    const t = metrics.totals || {};
    $("metric-total").textContent = String(t.runs || 0).padStart(2, "0");
    $("metric-time").innerHTML = `${Math.round(t.avg_ms || 0)} <span class="metric-unit">ms</span>`;
    $("metric-agents").innerHTML = `${Number(metrics.operational_agents || 5)} <span class="metric-unit">/ 05</span>`;
    $("metric-fusion").textContent = `v${versions.current || "1.0.0"}`;
    $("fusion-foot").textContent = "Version active LangGraph";
    $("admin-metrics").innerHTML = [
      ["Analyses", t.runs || 0], ["Tokens total", Number(t.tokens || 0).toLocaleString("fr-FR")],
      ["Tokens entrée", Number(t.prompt_tokens || 0).toLocaleString("fr-FR")], ["Tokens sortie", Number(t.completion_tokens || 0).toLocaleString("fr-FR")],
      ["Temps moyen", `${Math.round(t.avg_ms || 0)} ms`]
    ].map(([label,value]) => `<div class="admin-metric"><small>${safeText(label)}</small><strong>${safeText(value)}</strong></div>`).join("");
    $("admin-users").innerHTML = `<strong>Utilisateurs (${(users.users || []).length})</strong><br>` + (users.users || []).map(u => `${safeText(u.display_name)} — ${safeText(u.email)} · ${safeText(u.role)} · ${u.incidents} analyse(s)`).join("<br>") || "Aucun utilisateur.";
    $("version-history").innerHTML = `<strong>Historique versions</strong><br>` + (versions.versions || []).map(v => `v${safeText(v.version)} · ${safeText(v.change_type)} · ${safeText(v.notes)} · ${safeText(v.created_at).slice(0,10)}`).join("<br>");
    historyRows = incidents.items || [];
    const tbody = $("activity-body"); tbody.replaceChildren();
    historyRows.slice(0, 12).forEach((item) => {
      const tr = document.createElement("tr");
      const message = String(item.message || "");
      const statusClass = item.governance_status === "VALID" ? "" : item.governance_status === "INVALID" ? "error" : "pending";
      const statusText = item.governance_status === "VALID" ? "Validé" : item.governance_status === "INVALID" ? "À revoir" : "Local";
      tr.innerHTML = `<td>${safeText(item.incident_id)}</td><td title="${safeText(message)}">${safeText(message.length > 54 ? message.slice(0,54) + "…" : message)}<br><small>${safeText(item.user_email || "")}</small></td><td><span class="table-mode">${safeText(modeLabels[item.mode] || item.mode)}</span></td><td><span class="table-status ${statusClass}">${safeText(statusText)}</span></td><td>${Number(item.duration_ms || 0)} ms</td>`;
      tbody.appendChild(tr);
    });
    if (!historyRows.length) tbody.innerHTML = '<tr class="table-empty"><td colspan="5">Aucune activité pour le moment.</td></tr>';
    $("version-current").textContent = `v${versions.current}`; $("version-chip").textContent = `v${versions.current}`; $("metric-fusion").textContent = `v${versions.current}`;
    $("tag-button").textContent = `Créer tag v${versions.current}`;
  } catch (error) { $("admin-metrics").textContent = error.message || "Métriques indisponibles."; }
}
$("create-version-button").addEventListener("click", async () => {
  const notes = $("version-notes").value.trim(), change_type = $("version-type").value;
  if (notes.length < 3) { $("ops-message").textContent = "Ajoutez un motif de 3 caractères minimum."; return; }
  if (!window.confirm(`Créer une version ${change_type.toUpperCase()} ?`)) return;
  try {
    const r = await fetch("/api/admin/versions", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({change_type,notes})});
    const d = await r.json(); if (!r.ok) throw new Error(d.detail || "Création impossible.");
    $("ops-message").textContent = d.message; $("version-notes").value = ""; await refreshOps(); await refreshAdminPanels();
  } catch(e) { $("ops-message").textContent = e.message; }
});
$("rollback-version-button").addEventListener("click", async () => {
  if (!window.confirm("Rollback logique vers la version précédente ? Le code source Git ne sera pas modifié.")) return;
  try {
    const r = await fetch("/api/admin/versions/rollback", {method:"POST"}), d = await r.json();
    if (!r.ok) throw new Error(d.detail || "Rollback impossible.");
    $("ops-message").textContent = d.message; await refreshOps(); await refreshAdminPanels();
  } catch(e) { $("ops-message").textContent = e.message; }
});

$("rollback-code-button").addEventListener("click", async () => {
  const tag = $("rollback-tag").value.trim();
  if (!tag) { $("ops-message").textContent = "Saisissez un tag SemVer existant, par exemple v1.0.0."; return; }
  const ok = window.confirm(`ATTENTION : le code local sera ramené au tag ${tag}. Une branche de sauvegarde sera créée. Continuer ?`);
  if (!ok) return;
  try {
    const r = await fetch("/api/git/rollback", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({tag,confirm:true})});
    const d = await r.json(); if (!r.ok) throw new Error(d.detail || "Rollback Git impossible.");
    $("ops-message").textContent = d.message; await refreshOps();
  } catch(e) { $("ops-message").textContent = e.message; }
});

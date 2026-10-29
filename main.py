import os
import time
import uuid
import subprocess
import shutil
import re
from pathlib import Path
import sqlite3, json, hmac, hashlib, secrets
from datetime import datetime, timezone
from typing import TypedDict

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from langgraph.graph import StateGraph, START, END

load_dotenv(override=False)

APP_NAME = "FusionFlow AI"
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/auto").strip()
WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("WEBHOOK_TIMEOUT_SECONDS", "20"))
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "").strip()  # owner/repo
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()
AGENT_VERSION = os.getenv("AGENT_VERSION", "1.0.0").strip()
GIT_SYNC_ENABLED = os.getenv("GIT_SYNC_ENABLED", "false").strip().lower() == "true"
KILL_SWITCH_ACTIVE = os.getenv("KILL_SWITCH_ACTIVE", "false").strip().lower() == "true"
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@fusionflow.local").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
AUTO_TAG_ON_VERSION = os.getenv("AUTO_TAG_ON_VERSION", "false").strip().lower() == "true"

PROJECT_ROOT = Path(__file__).resolve().parent

DATABASE_PATH = PROJECT_ROOT / os.getenv(
    "DATABASE_PATH", "fusionflow.db"
)


app = FastAPI(title=APP_NAME, version=AGENT_VERSION)
app.mount("/static", StaticFiles(directory=str(PROJECT_ROOT / "static")), name="static")


class IncidentRequest(BaseModel):
    message: str = Field(min_length=5, max_length=8000)
    mode: str = Field(default="minor", pattern="^(major|minor|patch)$")
    response_style: str = Field(default="detailed", pattern="^(brief|normal|detailed)$")


class IncidentState(TypedDict, total=False):
    message: str
    mode: str
    incident_id: str
    analysis: str
    severity: str
    recommended_steps: list[str]
    agent_status: str
    detection_status: str
    governance_status: str
    correction_attempts: int
    final_report: str
    agent_trace: list[dict[str, str]]
    error: str
    started_at: float
    duration_ms: int
    response_style: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int



def now_iso():
    return datetime.now(timezone.utc).isoformat()


def db_connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 240000).hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 240000).hex()
        return hmac.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


def audit(user_id, action, details):
    with db_connect() as conn:
        conn.execute("INSERT INTO audit_log(user_id,action,details,created_at) VALUES(?,?,?,?)", (user_id, action, str(details)[:1000], now_iso()))


def _version_stack(conn):
    """Pile des versions : une version normale empile, un rollback dépile."""
    stack = []
    for row in conn.execute("SELECT version,is_rollback FROM versions ORDER BY id ASC").fetchall():
        if row["is_rollback"]:
            if len(stack) > 1:
                stack.pop()
        else:
            stack.append(row["version"])
    return stack


def init_db():
    with db_connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY, incident_id TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, message TEXT NOT NULL, mode TEXT NOT NULL, response_style TEXT NOT NULL, severity TEXT, analysis TEXT, recommended_steps TEXT, governance_status TEXT, duration_ms INTEGER, prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY, version TEXT NOT NULL, change_type TEXT NOT NULL, notes TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL, is_rollback INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, user_id INTEGER, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        if ADMIN_PASSWORD:
            admin = conn.execute(
                "SELECT id FROM users WHERE email = ?",
                (ADMIN_EMAIL,)
            ).fetchone()

            password_hash = hash_password(ADMIN_PASSWORD)

            if admin:
                conn.execute(
                    """UPDATE users
                       SET password_hash = ?, role = 'admin',
                           display_name = 'FusionFlow Admin'
                       WHERE id = ?""",
                    (password_hash, admin["id"])
                )
            else:
                conn.execute(
                    """INSERT INTO users
                       (email, display_name, password_hash, role, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        ADMIN_EMAIL,
                        "FusionFlow Admin",
                        password_hash,
                        "admin",
                        now_iso()
                    )
                )
        if not conn.execute("SELECT id FROM versions").fetchone():
            conn.execute("INSERT INTO versions(version,change_type,notes,created_at) VALUES(?,?,?,?)", (AGENT_VERSION, "initial", "Version initiale", now_iso()))
        stack = _version_stack(conn)
        if stack:
            globals()["AGENT_VERSION"] = stack[-1]


def get_current_user(request: Request):
    token = request.cookies.get("fusionflow_session", "")
    if not token:
        raise HTTPException(status_code=401, detail="Connectez-vous pour continuer.")
    key = hashlib.sha256(token.encode()).hexdigest()
    with db_connect() as conn:
        row = conn.execute("SELECT u.id,u.email,u.display_name,u.role,s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=?", (key,)).fetchone()
        if not row or row["expires_at"] < now_iso():
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (key,))
            raise HTTPException(status_code=401, detail="Session expirée. Reconnectez-vous.")
        return dict(row)


def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Cette fonction est réservée à l'administrateur.")
    return user


def login_user(user_id, email, display_name, role, response):
    token = secrets.token_urlsafe(36)
    from datetime import timedelta
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with db_connect() as conn:
        conn.execute("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user_id, expires))
    response.set_cookie("fusionflow_session", token, httponly=True, samesite="lax", secure=os.getenv("COOKIE_SECURE", "false").lower()=="true", max_age=SESSION_DAYS*86400, path="/")
    audit(user_id, "login", role)
    return {"id": user_id, "email": email, "display_name": display_name, "role": role}


init_db()


def fallback_analysis(message: str, mode: str) -> dict:
    lower = message.lower()
    if any(word in lower for word in ("500", "crash", "down", "outage", "production", "database")):
        severity = "High"
    elif any(word in lower for word in ("slow", "timeout", "error", "failed", "bug")):
        severity = "Medium"
    else:
        severity = "Low"

    actions = {
        "major": [
            "Confirmer l'impact utilisateur et le périmètre de l'incident.",
            "Prioriser la stabilisation du service et préparer un rollback si nécessaire.",
            "Vérifier les logs, métriques et dépendances avant toute modification.",
            "Valider le rétablissement avec un test de santé et surveiller les métriques."
        ],
        "minor": [
            "Reproduire le problème dans un environnement contrôlé.",
            "Identifier la cause probable dans les logs et les changements récents.",
            "Appliquer un correctif ciblé puis lancer les tests concernés.",
            "Surveiller le service après déploiement."
        ],
        "patch": [
            "Isoler le composant et la condition qui déclenche le défaut.",
            "Préparer un patch minimal et réversible.",
            "Exécuter les tests unitaires et une vérification de non-régression.",
            "Documenter le changement et prévoir un retour arrière."
        ]
    }
    return {
        "severity": severity,
        "analysis": (
            f"Analyse initiale basée sur la description fournie. "
            f"Mode demandé : {mode.upper()}. Sévérité estimée : {severity}. "
            "Cette estimation est préliminaire et doit être confirmée par les logs et les tests."
        ),
        "recommended_steps": actions[mode]
    }



def detect_incident(state: IncidentState) -> IncidentState:
    message = state["message"].strip()
    valid = len(message) >= 10 and any(ch.isalpha() for ch in message)
    trace = list(state.get("agent_trace", []))
    trace.append({"agent": "Agent de Détection", "status": "SUCCESS" if valid else "INVALID", "detail": "Description recevable" if valid else "Description trop courte ou inexploitable"})
    return {**state, "detection_status": "VALID" if valid else "INVALID", "agent_trace": trace}


def diagnose_incident(state: IncidentState) -> IncidentState:
    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    if not OPENROUTER_API_KEY:
        result = fallback_analysis(state["message"], state["mode"])
        content = result["analysis"]
        severity = result["severity"]
        steps = result["recommended_steps"]
        status = "completed (fallback)"
    else:
        try:
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import HumanMessage, SystemMessage
            llm = ChatOpenAI(model=OPENROUTER_MODEL, api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1", temperature=0.2, timeout=25)
            style = state.get("response_style", "detailed")
            detail = {"brief": "Réponse courte, maximum 5 lignes.", "normal": "Réponse équilibrée avec explication et étapes.", "detailed": "Réponse très détaillée : contexte, hypothèses, étapes, risques et validation."}[style]
            prompt = f"Analyse cet incident en français. Donne SEVERITY: Low/Medium/High, ANALYSIS: diagnostic et hypothèses, STEPS: 4 étapes concrètes. {detail} Ne prétends jamais avoir exécuté une action. Mode: {state['mode'].upper()}. Incident: {state['message']}"
            response = llm.invoke([SystemMessage(content="Tu es un agent senior de diagnostic informatique. Distingue faits et hypothèses."), HumanMessage(content=prompt)])
            content = str(response.content).strip()
            usage = getattr(response, "usage_metadata", None) or {}
            meta_usage = getattr(response, "response_metadata", {}).get("token_usage", {})
            token_usage = {"prompt_tokens": int(usage.get("input_tokens", meta_usage.get("prompt_tokens", 0)) or 0), "completion_tokens": int(usage.get("output_tokens", meta_usage.get("completion_tokens", 0)) or 0), "total_tokens": int(usage.get("total_tokens", meta_usage.get("total_tokens", 0)) or 0)}
            lower = content.lower()
            severity = "High" if "severity: high" in lower else "Low" if "severity: low" in lower else "Medium"
            steps = [line.strip().lstrip("-• ") for line in content.splitlines() if line.strip().startswith(("-", "•"))]
            if not steps: steps = fallback_analysis(state["message"], state["mode"])["recommended_steps"]
            status = "completed"
        except Exception as exc:
            result = fallback_analysis(state["message"], state["mode"])
            content, severity, steps, status = result["analysis"], result["severity"], result["recommended_steps"], f"fallback ({type(exc).__name__})"
    if not token_usage["total_tokens"]:
        token_usage = {"prompt_tokens": max(1, len(state["message"]) // 4), "completion_tokens": max(1, len(content) // 4), "total_tokens": max(2, (len(state["message"]) + len(content)) // 4)}
    trace = list(state.get("agent_trace", []))
    trace.append({"agent": "Agent Diagnostic", "status": "SUCCESS", "detail": status})
    return {**state, "analysis": content, "severity": severity, "recommended_steps": steps[:6], "agent_status": status, "agent_trace": trace, **token_usage}


def govern_solution(state: IncidentState) -> IncidentState:
    # Governance checks completeness and dangerous/unsubstantiated actions; it does not execute code.
    text = (state.get("analysis", "") + " " + " ".join(state.get("recommended_steps", []))).lower()
    risky = any(term in text for term in ("delete all", "drop database", "disable security", "supprimer toutes les données"))
    enough = bool(state.get("analysis")) and len(state.get("recommended_steps", [])) >= 2
    valid = enough and not risky
    trace = list(state.get("agent_trace", []))
    trace.append({"agent": "Agent de Gouvernance", "status": "VALID" if valid else "INVALID", "detail": "Contrôles de cohérence réussis" if valid else "Solution incomplète ou action risquée"})
    return {**state, "governance_status": "VALID" if valid else "INVALID", "agent_trace": trace}


def correct_solution(state: IncidentState) -> IncidentState:
    # One safe correction pass; never claims execution or changes code.
    steps = list(state.get("recommended_steps", []))
    safety = "Vérifier les sauvegardes et l'impact avant toute action irréversible."
    if safety not in steps: steps.append(safety)
    trace = list(state.get("agent_trace", []))
    trace.append({"agent": "Agent de Correction", "status": "SUCCESS", "detail": "Proposition révisée (1 tentative maximum)"})
    return {**state, "recommended_steps": steps[:6], "correction_attempts": int(state.get("correction_attempts", 0)) + 1, "agent_trace": trace}


def report_incident(state: IncidentState) -> IncidentState:
    status = state.get("governance_status", "NOT_CHECKED")
    report = (f"Incident {state.get('incident_id', 'NON_FOURNI')} — {state.get('severity', 'À confirmer')}\n"
              f"Méthode: {state.get('mode', '').upper()}\nGouvernance: {status}\n\n"
              f"Diagnostic:\n{state.get('analysis', '')}\n\nProchaines étapes:\n" +
              "\n".join(f"{i+1}. {step}" for i, step in enumerate(state.get("recommended_steps", []))) +
              "\n\nExécution réelle: NON EXÉCUTÉE. Les actions proposées doivent être vérifiées avant application.")
    trace = list(state.get("agent_trace", []))
    trace.append({"agent": "Agent de Reporting", "status": "SUCCESS", "detail": "Rapport final généré"})
    return {**state, "final_report": report, "agent_trace": trace}


def route_after_detection(state: IncidentState) -> str:
    return "report" if state.get("detection_status") != "VALID" else "diagnose"


def route_after_governance(state: IncidentState) -> str:
    if state.get("governance_status") == "INVALID" and int(state.get("correction_attempts", 0)) < 1:
        return "correct"
    return "report"


graph_builder = StateGraph(IncidentState)
graph_builder.add_node("detect", detect_incident)
graph_builder.add_node("diagnose", diagnose_incident)
graph_builder.add_node("governance", govern_solution)
graph_builder.add_node("correct", correct_solution)
graph_builder.add_node("report", report_incident)
graph_builder.add_edge(START, "detect")
graph_builder.add_conditional_edges("detect", route_after_detection, {"diagnose": "diagnose", "report": "report"})
graph_builder.add_edge("diagnose", "governance")
graph_builder.add_conditional_edges("governance", route_after_governance, {"correct": "correct", "report": "report"})
graph_builder.add_edge("correct", "governance")
graph_builder.add_edge("report", END)
incident_graph = graph_builder.compile()


@app.get("/")
def home():
    return FileResponse(PROJECT_ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "app": APP_NAME, "llm_configured": bool(OPENROUTER_API_KEY), "version": AGENT_VERSION, "langgraph_agents": 5, "timestamp": now_iso(), "auth_required": True}


@app.post("/api/auth/register")
def register(payload: dict, response: Response):
    email = str(payload.get("email", "")).strip().lower()
    password = str(payload.get("password", ""))
    display_name = str(payload.get("display_name", "")).strip()[:80] or email.split("@")[0]
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Adresse e-mail invalide.")
    if len(password) < 10:
        raise HTTPException(status_code=422, detail="Le mot de passe doit contenir au moins 10 caractères.")
    if email == ADMIN_EMAIL:
        raise HTTPException(status_code=409, detail="Cette adresse est réservée à l'administrateur.")
    try:
        with db_connect() as conn:
            cur = conn.execute("INSERT INTO users(email,display_name,password_hash,role,created_at) VALUES(?,?,?,?,?)", (email, display_name, hash_password(password), "user", now_iso()))
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Cette adresse est déjà inscrite.")
    return login_user(user_id, email, display_name, "user", response)


@app.post("/api/auth/login")
def login(payload: dict, response: Response):
    email, password = str(payload.get("email", "")).strip().lower(), str(payload.get("password", ""))
    with db_connect() as conn:
        row = conn.execute("SELECT id,email,display_name,password_hash,role FROM users WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="E-mail ou mot de passe incorrect.")
    return login_user(row["id"], row["email"], row["display_name"], row["role"], response)


@app.get("/api/auth/me")
def auth_me(user=Depends(get_current_user)):
    return user


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, user=Depends(get_current_user)):
    token = request.cookies.get("fusionflow_session", "")
    with db_connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))
    response.delete_cookie("fusionflow_session", path="/")
    audit(user["id"], "logout", "session fermée")
    return {"status": "ok"}


@app.post("/api/incidents")
def create_incident(request: IncidentRequest, user=Depends(get_current_user)):
    started = time.perf_counter()
    incident_id = "INC-" + uuid.uuid4().hex[:8].upper()
    state: IncidentState = {"message": request.message.strip(), "mode": request.mode, "response_style": request.response_style, "incident_id": incident_id, "started_at": started}
    try:
        result = incident_graph.invoke(state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph execution failed: {type(exc).__name__}") from exc
    duration_ms = round((time.perf_counter() - started) * 1000)
    created_at = now_iso()
    with db_connect() as conn:
        conn.execute("INSERT INTO incidents(incident_id,user_id,message,mode,response_style,severity,analysis,recommended_steps,governance_status,duration_ms,prompt_tokens,completion_tokens,total_tokens,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (incident_id,user["id"],request.message.strip(),request.mode,request.response_style,result.get("severity","Medium"),result.get("analysis",""),json.dumps(result.get("recommended_steps",[]),ensure_ascii=False),result.get("governance_status","unknown"),duration_ms,result.get("prompt_tokens",0),result.get("completion_tokens",0),result.get("total_tokens",0),created_at))
    audit(user["id"], "incident_analyzed", incident_id)
    return {"incident_id":incident_id,"user_email":user["email"],"message":request.message.strip(),"mode":request.mode,"response_style":request.response_style,"severity":result.get("severity","Medium"),"analysis":result.get("analysis",""),"recommended_steps":result.get("recommended_steps",[]),"agent_status":result.get("agent_status","unknown"),"detection_status":result.get("detection_status","unknown"),"governance_status":result.get("governance_status","unknown"),"correction_attempts":result.get("correction_attempts",0),"final_report":result.get("final_report",""),"agent_trace":result.get("agent_trace",[]),"duration_ms":duration_ms,"prompt_tokens":result.get("prompt_tokens",0),"completion_tokens":result.get("completion_tokens",0),"total_tokens":result.get("total_tokens",0),"created_at":created_at,"warning":result.get("error")}


@app.get("/api/incidents/mine")
def my_incidents(user=Depends(get_current_user)):
    with db_connect() as conn:
        rows = conn.execute("SELECT incident_id,message,mode,response_style,severity,analysis,recommended_steps,governance_status,duration_ms,prompt_tokens,completion_tokens,total_tokens,created_at FROM incidents WHERE user_id=? ORDER BY id DESC LIMIT 50",(user["id"],)).fetchall()
    items=[]
    for row in rows:
        item=dict(row); item["recommended_steps"]=json.loads(item["recommended_steps"] or "[]"); items.append(item)
    return {"items":items}


@app.get("/api/admin/users")
def admin_users(user=Depends(require_admin)):
    with db_connect() as conn:
        rows=conn.execute("SELECT u.id,u.email,u.display_name,u.role,u.created_at,COUNT(i.id) incidents FROM users u LEFT JOIN incidents i ON i.user_id=u.id GROUP BY u.id ORDER BY u.id DESC").fetchall()
    return {"users":[dict(r) for r in rows]}


@app.get("/api/admin/metrics")
def admin_metrics(user=Depends(require_admin)):
    with db_connect() as conn:
        totals=conn.execute("SELECT COUNT(*) runs,COALESCE(SUM(total_tokens),0) tokens,COALESCE(SUM(prompt_tokens),0) prompt_tokens,COALESCE(SUM(completion_tokens),0) completion_tokens,COALESCE(AVG(duration_ms),0) avg_ms FROM incidents").fetchone()
    return {"totals":dict(totals), "operational_agents": len(AGENT_CARDS)}


@app.get("/api/admin/incidents")
def admin_incidents(user=Depends(require_admin)):
    with db_connect() as conn:
        rows = conn.execute("""SELECT i.incident_id,i.message,i.mode,i.response_style,i.severity,i.analysis,
            i.recommended_steps,i.governance_status,i.duration_ms,i.prompt_tokens,i.completion_tokens,
            i.total_tokens,i.created_at,u.email AS user_email
            FROM incidents i JOIN users u ON u.id=i.user_id ORDER BY i.id DESC LIMIT 100""").fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["recommended_steps"] = json.loads(item.get("recommended_steps") or "[]")
        items.append(item)
    return {"items": items}


@app.get("/api/admin/versions")
def list_versions(user=Depends(require_admin)):
    with db_connect() as conn:
        rows=conn.execute("SELECT version,change_type,notes,created_at,is_rollback FROM versions ORDER BY id DESC LIMIT 50").fetchall()
    return {"current":AGENT_VERSION,"versions":[dict(r) for r in rows]}


class VersionRequest(BaseModel):
    change_type: str = Field(pattern="^(major|minor|patch)$")
    notes: str = Field(min_length=3,max_length=500)


def bump_version(version, kind):
    m=re.fullmatch(r"(\d+)\.(\d+)\.(\d+)",version)
    if not m: raise HTTPException(status_code=400,detail="AGENT_VERSION doit respecter SemVer.")
    a,b,c=map(int,m.groups())
    if kind=="major": a,b,c=a+1,0,0
    elif kind=="minor": b,c=b+1,0
    else: c+=1
    return f"{a}.{b}.{c}"


@app.post("/api/admin/versions")
def create_version(payload: VersionRequest,user=Depends(require_admin)):
    global AGENT_VERSION
    if KILL_SWITCH_ACTIVE: raise HTTPException(status_code=423,detail="Kill Switch actif.")
    previous=AGENT_VERSION; AGENT_VERSION=bump_version(AGENT_VERSION,payload.change_type)
    with db_connect() as conn: conn.execute("INSERT INTO versions(version,change_type,notes,created_by,created_at) VALUES(?,?,?,?,?)",(AGENT_VERSION,payload.change_type,payload.notes,user["id"],now_iso()))
    audit(user["id"],"version_created",f"{previous} -> {AGENT_VERSION}")
    message=f"Version logique v{AGENT_VERSION} créée. Pour publier le code, créez un commit/tag Git."
    tag=None; tag_warning=None
    if AUTO_TAG_ON_VERSION:
        try:
            tag=_create_release_tag(); audit(user["id"],"git_tag",tag)
            message=f"Version v{AGENT_VERSION} créée et tag Git {tag} créé localement. Utilisez Push tag pour déclencher le CD."
        except HTTPException as exc:
            tag_warning=str(exc.detail)
            message=f"Version v{AGENT_VERSION} créée, mais le tag Git n'a pas été créé : {tag_warning}"
    return {"version":AGENT_VERSION,"previous":previous,"tag":tag,"tag_warning":tag_warning,"message":message}


@app.post("/api/admin/versions/rollback")
def rollback_version(user=Depends(require_admin)):
    global AGENT_VERSION
    with db_connect() as conn:
        stack = _version_stack(conn)
        if len(stack) < 2:
            raise HTTPException(status_code=409, detail="Aucune version précédente disponible.")
        current, previous = stack[-1], stack[-2]
        AGENT_VERSION = previous
        conn.execute("INSERT INTO versions(version,change_type,notes,created_by,created_at,is_rollback) VALUES(?,?,?,?,?,1)", (previous, "rollback", f"Retour logique depuis {current}; code Git non modifié.", user["id"], now_iso()))
    audit(user["id"], "version_rollback", f"{current} -> {previous}")
    return {"version": AGENT_VERSION, "message": f"Version logique ramenée à v{AGENT_VERSION}. Le code Git n'a pas été restauré automatiquement."}


class GitRollbackRequest(BaseModel):
    tag: str = Field(pattern=r"^v?\d+\.\d+\.\d+$")
    confirm: bool = False


@app.post("/api/git/rollback")
def git_rollback(payload: GitRollbackRequest, user=Depends(require_admin)):
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="Confirmez explicitement le rollback.")
    if KILL_SWITCH_ACTIVE:
        raise HTTPException(status_code=423, detail="Kill Switch actif : rollback suspendu.")
    if not GIT_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="Actions Git désactivées. Configurez GIT_SYNC_ENABLED=true.")
    if not _git_available():
        raise HTTPException(status_code=400, detail="Ce dossier n'est pas un dépôt Git.")
    status = _git("status", "--porcelain")
    if status.stdout.strip():
        raise HTTPException(status_code=409, detail="Le dépôt contient des changements locaux. Sauvegardez/committez-les avant rollback.")
    branch = _git("branch", "--show-current")
    if branch.returncode != 0 or not branch.stdout.strip():
        raise HTTPException(status_code=400, detail="Branche active introuvable.")
    tag = payload.tag if payload.tag.startswith("v") else "v" + payload.tag
    target = _git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if target.returncode != 0:
        raise HTTPException(status_code=404, detail=f"Le tag {tag} n'existe pas localement.")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_branch = f"backup-before-rollback-{stamp}"
    backup = _git("branch", backup_branch)
    if backup.returncode != 0:
        raise HTTPException(status_code=400, detail=(backup.stderr or backup.stdout).strip()[:1000])
    result = _git("reset", "--hard", tag, timeout=30)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout).strip()[:1000])
    audit(user["id"], "git_rollback", f"branch={branch}; tag={tag}; backup={backup_branch}")
    return {"status":"success", "tag":tag, "branch":branch, "backup_branch":backup_branch, "message":f"Code local ramené à {tag} sur {branch}. Branche de sauvegarde créée : {backup_branch}. Le push n'est pas automatique; vérifiez les tests avant de publier."}


AGENT_CARDS = [
    {"id": "detection", "name": "Agent de Détection", "role": "Valide la qualité de la description de l'incident", "owner": "LangGraph", "tools": ["Règles de validation"], "limits": "Ne diagnostique pas sans données", "kpi": "Validité de l'entrée", "version": AGENT_VERSION},
    {"id": "diagnostic", "name": "Agent Diagnostic", "role": "Analyse les causes possibles et propose des étapes", "owner": "LangGraph", "tools": ["LLM optionnel", "Contexte incident"], "limits": "Ne prétend pas avoir exécuté les corrections", "kpi": "Complétude du diagnostic", "version": AGENT_VERSION},
    {"id": "governance", "name": "Agent de Gouvernance", "role": "Vérifie cohérence, risques et complétude", "owner": "LangGraph", "tools": ["Règles de gouvernance"], "limits": "Seul cet agent décide VALID/INVALID", "kpi": "Risques détectés", "version": AGENT_VERSION},
    {"id": "correction", "name": "Agent de Correction", "role": "Révise une proposition après rejet", "owner": "LangGraph", "tools": ["Retour gouvernance"], "limits": "Une tentative; ne marque pas lui-même VALID", "kpi": "Correction contrôlée", "version": AGENT_VERSION},
    {"id": "reporting", "name": "Agent de Reporting", "role": "Produit le rapport final traçable", "owner": "LangGraph", "tools": ["Résultats du graphe"], "limits": "Ne fabrique pas de tests ni d'exécutions", "kpi": "Complétude du rapport", "version": AGENT_VERSION},
]


def _git(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    if not shutil.which("git"):
        raise RuntimeError("Git n'est pas installé ou n'est pas dans PATH.")
    return subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _git_available() -> bool:
    try:
        result = _git("rev-parse", "--is-inside-work-tree")
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False


@app.get("/api/ops/status")
def ops_status(user=Depends(require_admin)):
    git_info = {"available": False, "branch": None, "remote": None, "changed_files": 0, "clean": None, "sync_enabled": GIT_SYNC_ENABLED}
    if _git_available():
        branch = _git("branch", "--show-current")
        remote = _git("remote", "get-url", "origin")
        remote_value = remote.stdout.strip() if remote.returncode == 0 else None
        # Never return credentials embedded in a remote URL to the browser.
        if remote_value and "@" in remote_value and "://" in remote_value:
            prefix, remainder = remote_value.split("://", 1)
            if "@" in remainder:
                remote_value = prefix + "://***@" + remainder.split("@", 1)[1]
        status = _git("status", "--porcelain")
        git_info.update({
            "available": True,
            "branch": branch.stdout.strip() if branch.returncode == 0 else "(detached)",
            "remote": remote_value,
            "changed_files": len([line for line in status.stdout.splitlines() if line.strip()]),
            "clean": not bool(status.stdout.strip()),
        })
    github = {"configured": bool(GITHUB_REPOSITORY), "repository": GITHUB_REPOSITORY or None, "workflow_status": "unknown", "workflow_url": None, "workflow_name": None}
    if GITHUB_REPOSITORY:
        try:
            headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
            if GITHUB_TOKEN:
                headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
            with httpx.Client(timeout=8, headers=headers) as client:
                response = client.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/runs", params={"per_page": 1})
            if response.status_code == 200:
                runs = response.json().get("workflow_runs", [])
                if runs:
                    run = runs[0]
                    github.update({"workflow_status": f"{run.get('status', 'unknown')} / {run.get('conclusion') or 'in progress'}", "workflow_url": run.get("html_url"), "workflow_name": run.get("name")})
                else:
                    github["workflow_status"] = "no_runs"
            else:
                github["workflow_status"] = f"api_http_{response.status_code}"
        except Exception as exc:
            github["workflow_status"] = f"unavailable: {type(exc).__name__}"
    return {
        "version": AGENT_VERSION,
        "agents": [dict(card, version=AGENT_VERSION) for card in AGENT_CARDS],
        "git": git_info,
        "github": github,
        "ci_cd": {"workflow_file": ".github/workflows/ci.yml", "note": "Le CI démarre sur push et pull request. LangGraph orchestre les agents localement; le déploiement CD doit être configuré pour votre hébergeur."},
        "safety": {"kill_switch_active": KILL_SWITCH_ACTIVE, "rollback": "see RUNBOOK.md", "approval_required_for_deploy": True},
    }


class KillSwitchRequest(BaseModel):
    enabled: bool


@app.post("/api/ops/kill-switch")
def set_kill_switch(request: KillSwitchRequest, user=Depends(require_admin)):
    global KILL_SWITCH_ACTIVE
    KILL_SWITCH_ACTIVE = request.enabled
    return {"active": KILL_SWITCH_ACTIVE, "message": "Kill Switch activé : actions automatisées suspendues." if KILL_SWITCH_ACTIVE else "Kill Switch désactivé."}


@app.post("/api/git/pull")
def git_pull(user=Depends(require_admin)):
    if KILL_SWITCH_ACTIVE:
        raise HTTPException(status_code=423, detail="Kill Switch actif : Pull suspendu.")
    if not GIT_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="Pull désactivé. Configurez GIT_SYNC_ENABLED=true après avoir vérifié le dépôt local.")
    if not _git_available():
        raise HTTPException(status_code=400, detail="Ce dossier n'est pas un dépôt Git. Clonez ou initialisez le dépôt avant le Pull.")
    status = _git("status", "--porcelain")
    if status.stdout.strip():
        raise HTTPException(status_code=409, detail="Des modifications locales existent. Committez-les ou sauvegardez-les avant le Pull.")
    result = _git("pull", "--ff-only", timeout=60)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout).strip()[:1000])
    return {"status": "success", "message": (result.stdout or "Pull terminé.").strip()}


@app.post("/api/git/push")
def git_push(user=Depends(require_admin)):
    if KILL_SWITCH_ACTIVE:
        raise HTTPException(status_code=423, detail="Kill Switch actif : Push suspendu.")
    if not GIT_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="Push désactivé. Configurez GIT_SYNC_ENABLED=true après avoir vérifié le dépôt local.")
    if not _git_available():
        raise HTTPException(status_code=400, detail="Ce dossier n'est pas un dépôt Git. Clonez ou initialisez le dépôt avant le Push.")
    branch = _git("branch", "--show-current")
    if branch.returncode != 0 or not branch.stdout.strip():
        raise HTTPException(status_code=400, detail="Aucune branche active. Quittez l'état detached HEAD avant le Push.")
    result = _git("push", "origin", branch.stdout.strip(), timeout=60)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout).strip()[:1000])
    return {"status": "success", "message": (result.stdout or f"Push terminé sur {branch.stdout.strip()}.").strip()}


def _create_release_tag() -> str:
    """Crée le tag annoté vX.Y.Z pour AGENT_VERSION. Lève HTTPException si impossible."""
    if KILL_SWITCH_ACTIVE:
        raise HTTPException(status_code=423, detail="Kill Switch actif : création de tag suspendue.")
    if not GIT_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="Actions Git désactivées. Configurez GIT_SYNC_ENABLED=true.")
    if not _git_available():
        raise HTTPException(status_code=400, detail="Ce dossier n'est pas un dépôt Git ou Git n'est pas installé.")
    if not re.fullmatch(r"\d+\.\d+\.\d+", AGENT_VERSION):
        raise HTTPException(status_code=400, detail="AGENT_VERSION doit respecter SemVer, par exemple 1.2.3.")
    status = _git("status", "--porcelain")
    if status.stdout.strip():
        raise HTTPException(status_code=409, detail="Des changements locaux existent. Committez-les avant de créer un tag de release.")
    tag = f"v{AGENT_VERSION}"
    existing = _git("rev-parse", "--verify", f"refs/tags/{tag}")
    if existing.returncode == 0:
        raise HTTPException(status_code=409, detail=f"Le tag {tag} existe déjà.")
    result = _git("tag", "-a", tag, "-m", f"FusionFlow AI release {tag}")
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout).strip()[:1000])
    return tag


@app.post("/api/git/tag")
def create_git_tag(user=Depends(require_admin)):
    tag = _create_release_tag()
    audit(user["id"], "git_tag", tag)
    return {"status": "success", "tag": tag, "message": f"Tag {tag} créé localement. Utilisez Push tag pour l'envoyer à GitHub."}


@app.post("/api/git/push-tag")
def push_git_tag(user=Depends(require_admin)):
    if KILL_SWITCH_ACTIVE:
        raise HTTPException(status_code=423, detail="Kill Switch actif : Push tag suspendu.")
    if not GIT_SYNC_ENABLED:
        raise HTTPException(status_code=403, detail="Actions Git désactivées. Configurez GIT_SYNC_ENABLED=true.")
    if not _git_available():
        raise HTTPException(status_code=400, detail="Ce dossier n'est pas un dépôt Git ou Git n'est pas installé.")
    tag = f"v{AGENT_VERSION}"
    existing = _git("rev-parse", "--verify", f"refs/tags/{tag}")
    if existing.returncode != 0:
        raise HTTPException(status_code=404, detail=f"Le tag {tag} n'existe pas localement. Créez-le d'abord.")
    result = _git("push", "origin", tag, timeout=60)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout).strip()[:1000])
    audit(user["id"], "git_push_tag", tag)
    return {"status": "success", "tag": tag, "message": f"Tag {tag} envoyé vers origin."}

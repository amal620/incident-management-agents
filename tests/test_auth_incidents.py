def test_health_and_static(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["langgraph_agents"] == 5 and body["version"] == "1.0.0"
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_register_login_me_logout(client):
    r = client.post("/api/auth/register", json={"email": "a@test.local", "password": "LongPassword1"})
    assert r.status_code == 200 and r.json()["role"] == "user"
    assert client.get("/api/auth/me").json()["email"] == "a@test.local"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401  # session supprimée
    r = client.post("/api/auth/login", json={"email": "a@test.local", "password": "LongPassword1"})
    assert r.status_code == 200


def test_register_validation(client):
    assert client.post("/api/auth/register", json={"email": "pas-un-email", "password": "LongPassword1"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "b@test.local", "password": "court"}).status_code == 422
    assert client.post("/api/auth/register", json={"email": "admin@test.local", "password": "LongPassword1"}).status_code == 409
    ok = {"email": "c@test.local", "password": "LongPassword1"}
    assert client.post("/api/auth/register", json=ok).status_code == 200
    assert client.post("/api/auth/register", json=ok).status_code == 409  # doublon


def test_login_wrong_password(client):
    assert client.post("/api/auth/login", json={"email": "admin@test.local", "password": "mauvais"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "inconnu@test.local", "password": "x"}).status_code == 401


def test_endpoints_require_login(client):
    assert client.post("/api/incidents", json={"message": "Serveur en panne"}).status_code == 401
    assert client.get("/api/incidents/mine").status_code == 401
    assert client.get("/api/admin/metrics").status_code == 401


def test_roles_user_cannot_use_admin_endpoints(user):
    for method, url in [
        ("get", "/api/admin/metrics"), ("get", "/api/admin/users"), ("get", "/api/admin/incidents"),
        ("get", "/api/admin/versions"), ("get", "/api/ops/status"),
        ("post", "/api/admin/versions"), ("post", "/api/admin/versions/rollback"),
        ("post", "/api/ops/kill-switch"), ("post", "/api/git/pull"), ("post", "/api/git/push"),
        ("post", "/api/git/tag"), ("post", "/api/git/push-tag"), ("post", "/api/git/rollback"),
    ]:
        r = getattr(user, method)(url, json={}) if method == "post" else user.get(url)
        assert r.status_code == 403, f"{method.upper()} {url} -> {r.status_code}"


def test_incident_langgraph_workflow(user):
    r = user.post("/api/incidents", json={"message": "Le serveur de paiement répond lentement avec des erreurs 500", "mode": "minor"})
    assert r.status_code == 200
    body = r.json()
    assert body["incident_id"].startswith("INC-")
    assert body["detection_status"] == "VALID" and body["governance_status"] == "VALID"
    assert body["recommended_steps"] and body["final_report"]
    agents = [step["agent"] for step in body["agent_trace"]]
    assert "Agent de Détection" in agents and "Agent Diagnostic" in agents and "Agent de Gouvernance" in agents
    assert "Agent de Reporting" in agents


def test_incident_validation(user):
    assert user.post("/api/incidents", json={"message": "x"}).status_code == 422
    assert user.post("/api/incidents", json={"message": "Message assez long", "mode": "huge"}).status_code == 422


def test_history_is_private_and_admin_sees_all(app_module, make_user, admin):
    u1, u2 = make_user("u1@test.local"), make_user("u2@test.local")
    u1.post("/api/incidents", json={"message": "Incident de l'utilisateur un"})
    u2.post("/api/incidents", json={"message": "Incident de l'utilisateur deux"})
    mine1 = u1.get("/api/incidents/mine").json()["items"]
    assert len(mine1) == 1 and "un" in mine1[0]["message"]
    assert len(u2.get("/api/incidents/mine").json()["items"]) == 1
    all_items = admin.get("/api/admin/incidents").json()["items"]
    assert len(all_items) == 2
    assert admin.get("/api/admin/metrics").json()["totals"]["runs"] == 2
    emails = [u["email"] for u in admin.get("/api/admin/users").json()["users"]]
    assert {"admin@test.local", "u1@test.local", "u2@test.local"} <= set(emails)

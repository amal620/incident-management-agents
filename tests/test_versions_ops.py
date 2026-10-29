import pytest
from fastapi import HTTPException

from conftest import ADMIN


def bump(admin, kind, notes="changement de test"):
    r = admin.post("/api/admin/versions", json={"change_type": kind, "notes": notes})
    assert r.status_code == 200, r.text
    return r.json()["version"]


def test_bump_version_unit(app_module):
    assert app_module.bump_version("1.2.3", "patch") == "1.2.4"
    assert app_module.bump_version("1.2.3", "minor") == "1.3.0"
    assert app_module.bump_version("1.2.3", "major") == "2.0.0"
    with pytest.raises(HTTPException):
        app_module.bump_version("1.2", "patch")


def test_semver_major_minor_patch(admin):
    assert bump(admin, "patch") == "1.0.1"
    assert bump(admin, "patch") == "1.0.2"
    assert bump(admin, "minor") == "1.1.0"   # patch remis à 0
    assert bump(admin, "patch") == "1.1.1"
    assert bump(admin, "major") == "2.0.0"   # minor et patch remis à 0
    data = admin.get("/api/admin/versions").json()
    assert data["current"] == "2.0.0"
    assert [v["version"] for v in data["versions"]][:2] == ["2.0.0", "1.1.1"]


def test_version_validation(admin):
    assert admin.post("/api/admin/versions", json={"change_type": "huge", "notes": "test valide"}).status_code == 422
    assert admin.post("/api/admin/versions", json={"change_type": "patch", "notes": "x"}).status_code == 422


def test_rollback_one_level(admin):
    bump(admin, "minor")
    bump(admin, "major")
    r = admin.post("/api/admin/versions/rollback")
    assert r.status_code == 200 and r.json()["version"] == "1.1.0"


def test_rollback_multi_level(admin):
    """Correction : on peut maintenant reculer plusieurs fois de suite."""
    for kind in ["patch", "minor", "major"]:
        bump(admin, kind)                          # 1.0.1, 1.1.0, 2.0.0
    got = [admin.post("/api/admin/versions/rollback").json()["version"] for _ in range(3)]
    assert got == ["1.1.0", "1.0.1", "1.0.0"]
    assert admin.post("/api/admin/versions/rollback").status_code == 409   # plus rien avant 1.0.0
    assert admin.get("/api/admin/versions").json()["current"] == "1.0.0"


def test_bump_after_rollback(admin):
    bump(admin, "minor")                           # 1.1.0
    bump(admin, "major")                           # 2.0.0
    admin.post("/api/admin/versions/rollback")     # 1.1.0
    assert bump(admin, "patch") == "1.1.1"
    assert admin.post("/api/admin/versions/rollback").json()["version"] == "1.1.0"


def test_rollback_without_history(admin):
    assert admin.post("/api/admin/versions/rollback").status_code == 409


def test_version_persists_after_restart(app_module, admin):
    bump(admin, "minor")
    bump(admin, "patch")
    admin.post("/api/admin/versions/rollback")     # 1.1.0
    app_module.AGENT_VERSION = "0.0.0"             # simule un redémarrage avec .env obsolète
    app_module.init_db()
    assert app_module.AGENT_VERSION == "1.1.0"


def test_kill_switch_blocks_actions(admin):
    assert admin.post("/api/ops/kill-switch", json={"enabled": True}).json()["active"] is True
    assert admin.post("/api/admin/versions", json={"change_type": "patch", "notes": "bloqué"}).status_code == 423
    for url in ["/api/git/pull", "/api/git/push", "/api/git/tag", "/api/git/push-tag"]:
        assert admin.post(url).status_code == 423, url
    assert admin.post("/api/git/rollback", json={"tag": "v1.0.0", "confirm": True}).status_code == 423
    admin.post("/api/ops/kill-switch", json={"enabled": False})
    assert admin.post("/api/admin/versions", json={"change_type": "patch", "notes": "débloqué"}).status_code == 200


def test_ops_status_and_agent_cards(admin):
    bump(admin, "minor")
    data = admin.get("/api/ops/status").json()
    assert data["version"] == "1.1.0"
    assert len(data["agents"]) == 5 and all(a["version"] == "1.1.0" for a in data["agents"])
    assert data["safety"]["kill_switch_active"] is False
    assert data["git"]["sync_enabled"] is False
    assert data["github"]["configured"] is False


def test_admin_login_creates_admin(client):
    r = client.post("/api/auth/login", json=ADMIN)
    assert r.json()["role"] == "admin"

"""Fixtures : chaque test a sa propre base SQLite et un état global propre.

Les variables d'environnement sont posées AVANT l'import de main (il lit .env/os.environ à l'import).
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_BOOT_DIR = tempfile.mkdtemp(prefix="fusionflow-boot-")
os.environ.update({
    "OPENROUTER_API_KEY": "",
    "ADMIN_EMAIL": "admin@test.local",
    "ADMIN_PASSWORD": "TestPassword123!",
    "AGENT_VERSION": "1.0.0",
    "GIT_SYNC_ENABLED": "false",
    "KILL_SWITCH_ACTIVE": "false",
    "AUTO_TAG_ON_VERSION": "false",
    "GITHUB_REPOSITORY": "",
    "GITHUB_TOKEN": "",
    "COOKIE_SECURE": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

ADMIN = {"email": "admin@test.local", "password": "TestPassword123!"}


@pytest.fixture()
def app_module(tmp_path, monkeypatch):
    """Module main avec une base vierge et les drapeaux remis à zéro."""
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "test.db")
    monkeypatch.setattr(main, "AGENT_VERSION", "1.0.0")
    monkeypatch.setattr(main, "KILL_SWITCH_ACTIVE", False)
    monkeypatch.setattr(main, "GIT_SYNC_ENABLED", False)
    monkeypatch.setattr(main, "AUTO_TAG_ON_VERSION", False)
    monkeypatch.setattr(main, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(main, "GITHUB_REPOSITORY", "")
    main.init_db()
    return main


@pytest.fixture()
def client(app_module):
    return TestClient(app_module.app)


@pytest.fixture()
def admin(app_module):
    c = TestClient(app_module.app)
    r = c.post("/api/auth/login", json=ADMIN)
    assert r.status_code == 200 and r.json()["role"] == "admin"
    return c


@pytest.fixture()
def make_user(app_module):
    def _make(email="user1@test.local", password="UserPass12345"):
        c = TestClient(app_module.app)
        r = c.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return c
    return _make


@pytest.fixture()
def user(make_user):
    return make_user()


def run_git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def git_repo(tmp_path, app_module, monkeypatch):
    """Dépôt Git local + remote 'origin' bare. main.PROJECT_ROOT pointe sur ce dépôt."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    remote.mkdir()
    work.mkdir()
    run_git(remote, "init", "--bare", "-b", "main")
    run_git(work, "init", "-b", "main")
    run_git(work, "config", "user.email", "ci@test.local")
    run_git(work, "config", "user.name", "CI Test")
    (work / "app.txt").write_text("v1\n")
    run_git(work, "add", ".")
    run_git(work, "commit", "-m", "initial")
    run_git(work, "remote", "add", "origin", str(remote))
    run_git(work, "push", "-u", "origin", "main")
    monkeypatch.setattr(app_module, "PROJECT_ROOT", work)
    monkeypatch.setattr(app_module, "GIT_SYNC_ENABLED", True)
    return {"work": work, "remote": remote}

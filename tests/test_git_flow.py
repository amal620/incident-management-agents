"""Cycle Git réel : dépôt local + remote bare temporaires (aucun accès réseau)."""
from conftest import run_git


def tags(work):
    return run_git(work, "tag", "-l").split()


def remote_tags(remote):
    return run_git(remote, "tag", "-l").split()


def test_git_actions_disabled_by_default(app_module, admin):
    app_module.GIT_SYNC_ENABLED = False
    for url in ["/api/git/pull", "/api/git/push", "/api/git/tag", "/api/git/push-tag"]:
        assert admin.post(url).status_code == 403, url


def test_not_a_git_repo(app_module, admin, tmp_path):
    app_module.GIT_SYNC_ENABLED = True
    app_module.PROJECT_ROOT = tmp_path      # dossier sans .git
    assert admin.post("/api/git/tag").status_code == 400
    assert admin.get("/api/ops/status").json()["git"]["available"] is False


def test_ops_status_reports_git(admin, git_repo):
    g = admin.get("/api/ops/status").json()["git"]
    assert g["available"] and g["branch"] == "main" and g["clean"] is True and g["changed_files"] == 0
    (git_repo["work"] / "new.txt").write_text("x")
    g = admin.get("/api/ops/status").json()["git"]
    assert g["clean"] is False and g["changed_files"] == 1


def test_manual_tag_then_push_tag(admin, git_repo):
    work, remote = git_repo["work"], git_repo["remote"]
    r = admin.post("/api/git/tag")
    assert r.status_code == 200 and r.json()["tag"] == "v1.0.0"
    assert "v1.0.0" in tags(work)
    assert admin.post("/api/git/tag").status_code == 409           # déjà existant
    assert admin.post("/api/git/push-tag").status_code == 200
    assert "v1.0.0" in remote_tags(remote)


def test_push_tag_requires_existing_tag(admin, git_repo):
    assert admin.post("/api/git/push-tag").status_code == 404


def test_tag_refused_with_local_changes(admin, git_repo):
    (git_repo["work"] / "dirty.txt").write_text("pas commité")
    assert admin.post("/api/git/tag").status_code == 409


def test_version_creation_auto_tags(app_module, admin, git_repo):
    app_module.AUTO_TAG_ON_VERSION = True
    r = admin.post("/api/admin/versions", json={"change_type": "minor", "notes": "nouvelle capacité"})
    body = r.json()
    assert r.status_code == 200 and body["version"] == "1.1.0" and body["tag"] == "v1.1.0" and body["tag_warning"] is None
    assert "v1.1.0" in tags(git_repo["work"])
    assert "v1.1.0" in body["message"]


def test_version_auto_tag_failure_does_not_block_version(app_module, admin, git_repo):
    app_module.AUTO_TAG_ON_VERSION = True
    (git_repo["work"] / "dirty.txt").write_text("pas commité")
    r = admin.post("/api/admin/versions", json={"change_type": "patch", "notes": "avec arbre sale"})
    body = r.json()
    assert r.status_code == 200 and body["version"] == "1.0.1"
    assert body["tag"] is None and body["tag_warning"]
    assert tags(git_repo["work"]) == []


def test_version_without_auto_tag_does_not_touch_git(admin, git_repo):
    r = admin.post("/api/admin/versions", json={"change_type": "patch", "notes": "sans tag auto"})
    assert r.json()["tag"] is None and tags(git_repo["work"]) == []


def test_push_and_pull(admin, git_repo, tmp_path):
    work, remote = git_repo["work"], git_repo["remote"]
    (work / "app.txt").write_text("v2\n")
    run_git(work, "commit", "-am", "second")
    assert admin.post("/api/git/push").status_code == 200
    assert run_git(remote, "rev-parse", "main") == run_git(work, "rev-parse", "HEAD")

    # un autre clone pousse un commit, puis la dashboard fait un pull
    other = tmp_path / "other"
    run_git(tmp_path, "clone", str(remote), str(other))
    run_git(other, "config", "user.email", "o@test.local")
    run_git(other, "config", "user.name", "Other")
    (other / "other.txt").write_text("depuis un autre poste\n")
    run_git(other, "add", ".")
    run_git(other, "commit", "-m", "autre poste")
    run_git(other, "push")
    assert admin.post("/api/git/pull").status_code == 200
    assert (work / "other.txt").exists()


def test_pull_refused_with_local_changes(admin, git_repo):
    (git_repo["work"] / "dirty.txt").write_text("x")
    assert admin.post("/api/git/pull").status_code == 409


def test_git_rollback(admin, git_repo):
    work = git_repo["work"]
    first = run_git(work, "rev-parse", "HEAD")
    assert admin.post("/api/git/tag").status_code == 200            # v1.0.0 sur le 1er commit
    (work / "app.txt").write_text("v2\n")
    run_git(work, "commit", "-am", "second")
    assert run_git(work, "rev-parse", "HEAD") != first

    assert admin.post("/api/git/rollback", json={"tag": "v1.0.0"}).status_code == 400            # sans confirmation
    assert admin.post("/api/git/rollback", json={"tag": "v9.9.9", "confirm": True}).status_code == 404
    assert admin.post("/api/git/rollback", json={"tag": "pas-un-tag", "confirm": True}).status_code == 422

    r = admin.post("/api/git/rollback", json={"tag": "v1.0.0", "confirm": True})
    assert r.status_code == 200
    assert run_git(work, "rev-parse", "HEAD") == first
    assert (work / "app.txt").read_text() == "v1\n"
    assert r.json()["backup_branch"] in run_git(work, "branch", "--list").replace("*", "").split()


def test_git_rollback_refused_with_local_changes(admin, git_repo):
    admin.post("/api/git/tag")
    (git_repo["work"] / "dirty.txt").write_text("x")
    assert admin.post("/api/git/rollback", json={"tag": "v1.0.0", "confirm": True}).status_code == 409


def test_full_release_flow(app_module, admin, git_repo):
    """Dashboard : version MINOR -> tag auto -> push tag (ce qui déclenche le CD sur GitHub)."""
    app_module.AUTO_TAG_ON_VERSION = True
    assert admin.post("/api/admin/versions", json={"change_type": "minor", "notes": "release complète"}).json()["tag"] == "v1.1.0"
    assert admin.post("/api/git/push-tag").status_code == 200
    assert "v1.1.0" in run_git(git_repo["remote"], "tag", "-l").split()
    assert admin.get("/api/admin/versions").json()["current"] == "1.1.0"

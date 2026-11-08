"""API checks use isolated storage and never create paid H sessions."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import store
from app.main import app, login_attempts


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(store, "DATA", Path(self.temp.name))
        self.storage.start()
        login_attempts.clear()
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.storage.stop()
        self.temp.cleanup()

    def register(self, email="alex@example.test"):
        response = self.client.post("/api/auth/register", json={"name": "Alex", "email": email, "password": "local-test-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_audit(self):
        body = {"name": "Checkout audit", "url": "http://127.0.0.1:8765/",
                "tasks": [{"title": "Checkout", "goal": "Find a product and report the checkout total."}],
                "personas": ["keyboard_only"], "repetitions": 1,
                "credentials": {"username": "fixture-user", "password": "fixture-site-secret", "headers": {"X-Test": "fixture-header-secret"}}}
        with patch("app.main.manager.start", return_value=True), patch("chrome.chrome_binary", return_value="chrome"), patch.dict("os.environ", {"HAI_API_KEY": "fixture-key"}):
            response = self.client.post("/api/audits", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_authentication_and_hashed_storage(self):
        self.assertEqual(self.client.get("/api/audits").status_code, 401)
        self.register()
        self.assertEqual(self.client.get("/api/auth/me").json()["name"], "Alex")
        with store.connect() as db:
            self.assertNotIn("local-test-password", db.execute("SELECT password FROM users").fetchone()[0])
            self.assertNotEqual(db.execute("SELECT token FROM sessions").fetchone()[0], self.client.cookies.get("humble_session"))
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": "alex@example.test", "password": "wrong-password"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": "alex@example.test", "password": "local-test-password"}).status_code, 200)

    def test_audits_are_private_including_exports_and_images(self):
        self.register()
        audit = self.create_audit()
        folder = store.DATA / "audits" / audit["id"]
        folder.mkdir(parents=True)
        (folder / "live.png").write_bytes(b"png-fixture")
        self.assertEqual(self.client.get(f"/api/audits/{audit['id']}/files/live.png").status_code, 200)
        self.client.post("/api/auth/logout")
        self.register("other@example.test")
        self.assertEqual(self.client.get("/api/audits").json(), [])
        self.assertEqual(self.client.get("/api/projects").json(), [])
        for path in [f"/api/audits/{audit['id']}", f"/api/audits/{audit['id']}/export", f"/api/audits/{audit['id']}/files/live.png", f"/api/audits/{audit['id']}/events"]:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post(f"/api/audits/{audit['id']}/stop").status_code, 404)

    def test_credentials_not_in_history_and_baseline_always_present(self):
        self.register()
        audit = self.create_audit()
        self.assertEqual(audit["config"]["personas"], ["baseline", "keyboard_only"])
        exported = self.client.get(f"/api/audits/{audit['id']}/export").text
        for secret in ["fixture-user", "fixture-site-secret", "fixture-header-secret"]:
            self.assertNotIn(secret, exported)
            self.assertNotIn(secret.encode(), (store.DATA / "humble.sqlite3").read_bytes())
        self.assertEqual(len(self.client.get("/api/projects").json()), 1)

    def test_controls_are_owned_and_persist_events(self):
        self.register()
        audit = self.create_audit()
        with patch("app.main.manager.control", return_value=True) as control:
            self.assertEqual(self.client.post(f"/api/audits/{audit['id']}/pause").status_code, 200)
            control.assert_called_once_with(audit["id"], "pause")
        store.update(audit["id"], status="completed")
        self.assertEqual(self.client.post(f"/api/audits/{audit['id']}/stop").status_code, 409)

    def test_cross_site_writes_rejected(self):
        self.assertEqual(self.client.post("/api/auth/logout", headers={"Origin": "https://other.test"}).status_code, 403)

    def test_busy_server_rejects_new_audit(self):
        self.register()
        with patch("app.main.manager.current", {"id": "another-audit"}), patch("chrome.chrome_binary", return_value="chrome"), patch.dict("os.environ", {"HAI_API_KEY": "fixture-key"}):
            response = self.client.post("/api/audits", json={"name": "New", "url": "https://example.test", "tasks": [{"title": "Read", "goal": "Read the main page heading."}], "personas": ["baseline"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get("/api/audits").json(), [])

    def vault_body(self):
        return {"name": "Demo shop QA", "url": "http://127.0.0.1:8765/login",
                "credentials": {"username": "vault-fixture-user", "password": "vault-fixture-secret",
                                "headers": {"X-Test-Token": "vault-fixture-header"}}}

    def test_vault_encryption_metadata_and_persistence(self):
        self.register()
        response = self.client.post("/api/vault", json=self.vault_body())
        self.assertEqual(response.status_code, 201, response.text)
        entry = response.json()
        self.assertEqual(entry["origin"], "http://127.0.0.1:8765")
        self.assertTrue(entry["has_login"])
        self.assertEqual(entry["header_count"], 1)
        listed = self.client.get("/api/vault").text
        with store.connect() as db:
            row = db.execute("SELECT * FROM vault_entries").fetchone()
            encrypted = row["secret"]
        for secret in ("vault-fixture-user", "vault-fixture-secret", "vault-fixture-header"):
            self.assertNotIn(secret, listed)
            self.assertNotIn(secret.encode(), encrypted)
            self.assertNotIn(secret.encode(), (store.DATA / "humble.sqlite3").read_bytes())
        self.assertEqual((store.DATA / "vault.key").stat().st_mode & 0o777, 0o600)
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"email": "alex@example.test", "password": "local-test-password"})
        self.assertEqual(self.client.get("/api/vault").json()[0]["id"], entry["id"])

    def test_audit_resolves_vault_without_exporting_secrets(self):
        self.register()
        entry = self.client.post("/api/vault", json=self.vault_body()).json()
        body = {"name": "Saved login test", "url": "http://127.0.0.1:8765/shop", "vault_id": entry["id"],
                "tasks": [{"title": "Read", "goal": "Read the main page heading."}], "personas": ["baseline"]}
        with patch("app.main.manager.start", return_value=True) as start, patch("chrome.chrome_binary", return_value="chrome"), patch.dict("os.environ", {"HAI_API_KEY": "fixture-key"}):
            response = self.client.post("/api/audits", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        auth = start.call_args.args[2]
        self.assertEqual(auth["login"]["password"], "vault-fixture-secret")
        self.assertEqual(auth["headers"]["X-Test-Token"], "vault-fixture-header")
        self.assertEqual(response.json()["config"]["vault_id"], entry["id"])
        self.assertEqual(response.json()["config"]["vault_name"], "Demo shop QA")
        report = self.client.get(f"/api/audits/{response.json()['id']}/export").text
        self.assertNotIn("vault-fixture-secret", report)
        self.assertNotIn("vault-fixture-user", report)
        self.assertNotIn("vault-fixture-header", report)
        with patch("app.main.manager.start") as start:
            body["url"] = "http://127.0.0.1:8766/shop"
            self.assertEqual(self.client.post("/api/audits", json=body).status_code, 422)
            body["url"] = "http://127.0.0.1:8765/shop"
            body["credentials"] = {"username": "other", "password": "other"}
            self.assertEqual(self.client.post("/api/audits", json=body).status_code, 422)
            start.assert_not_called()

    def test_vault_entries_cannot_be_used_or_changed_by_another_account(self):
        self.register()
        entry = self.client.post("/api/vault", json=self.vault_body()).json()
        self.client.post("/api/auth/logout")
        self.register("other@example.test")
        self.assertEqual(self.client.get("/api/vault").json(), [])
        self.assertEqual(self.client.put(f"/api/vault/{entry['id']}", json=self.vault_body()).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/vault/{entry['id']}").status_code, 404)
        response = self.client.post("/api/audits", json={"name": "Read", "url": "http://127.0.0.1:8765/", "vault_id": entry["id"], "tasks": [{"title": "Read", "goal": "Read the main page heading."}], "personas": ["baseline"]})
        self.assertEqual(response.status_code, 404)

    def test_vault_replacement_and_deletion(self):
        self.register()
        entry = self.client.post("/api/vault", json=self.vault_body()).json()
        body = self.vault_body()
        body["credentials"] = {"headers": {"Authorization": "fixture-replacement"}}
        updated = self.client.put(f"/api/vault/{entry['id']}", json=body)
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["has_login"])
        self.assertNotIn("fixture-replacement", updated.text)
        self.assertEqual(self.client.delete(f"/api/vault/{entry['id']}").status_code, 200)
        self.assertEqual(self.client.get("/api/vault").json(), [])



def test_steer_requires_a_running_agent_and_marks_the_session(self):
    from app.jobs import manager
    self.register()
    audit = self.create_audit()
    r = self.client.post(f"/api/audits/{audit['id']}/lanes/0/steer", json={"message": "Try the arrow keys"})
    self.assertEqual(r.status_code, 409, r.text)
    store.update(audit["id"], status="running", progress={"lanes": {"0": {"state": "running", "session_id": "sess-1", "persona": "keyboard_only"}}})
    with patch("hai_agents.Client") as client:
        r = self.client.post(f"/api/audits/{audit['id']}/lanes/0/steer", json={"message": "Try the arrow keys"})
    self.assertEqual(r.status_code, 200, r.text)
    self.assertIn("sess-1", manager.assisted)
    sent = client.return_value.__enter__.return_value.sessions.send_session_messages.call_args
    self.assertEqual(sent.args[0], "sess-1")
    self.assertEqual(sent.kwargs["request"].message, "Try the arrow keys")


AppTests.test_steer_requires_a_running_agent_and_marks_the_session = test_steer_requires_a_running_agent_and_marks_the_session



def test_delete_finished_audit_only(self):
    self.register()
    audit = self.create_audit()
    store.update(audit["id"], status="running")
    self.assertEqual(self.client.delete(f"/api/audits/{audit['id']}").status_code, 409)
    store.update(audit["id"], status="completed")
    folder = store.DATA / "audits" / audit["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.png").write_bytes(b"x")
    self.assertEqual(self.client.delete(f"/api/audits/{audit['id']}").status_code, 200)
    self.assertEqual(self.client.get(f"/api/audits/{audit['id']}").status_code, 404)
    self.assertFalse(folder.exists())


AppTests.test_delete_finished_audit_only = test_delete_finished_audit_only


if __name__ == "__main__":
    unittest.main()

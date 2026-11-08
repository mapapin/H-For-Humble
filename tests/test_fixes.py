"""Fix workflows: patch script, canned fixer, before/after crop, re-evaluation API. No H sessions, no Chrome."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import store
from app.main import app, login_attempts
from fixes import CannedFixer, DevinFixer, FallbackFixer, merge_patches, patch_script
from render import crop_to_content, focus_selectors, summarize_changes

DEMO = Path(__file__).resolve().parents[1] / "poc" / "fixes" / "demo_shop.json"
SITE = {"start_url": "http://127.0.0.1:8765/"}


def rows(**rates):
    return [{"task": "t", "persona": p, "success_rate": r} for p, r in rates.items()]


class FixTests(unittest.TestCase):
    def test_devin_publishes_session_before_waiting_for_output(self):
        fixer = DevinFixer(api_key="test", org_id="test")
        self.addCleanup(fixer.http.close)
        fixer.build_prompt = Mock(return_value="test")
        fixer.on_progress = Mock()
        created = Mock()
        created.status_code = 200
        created.json.return_value = {"session_id": "test-session", "url": "https://app.devin.ai/sessions/test"}
        finished = Mock()
        finished.json.return_value = {"status": "exit", "status_detail": "finished", "structured_output": {"fixes": []}}
        with patch.object(fixer.http, "post", return_value=created), patch.object(fixer.http, "get", return_value=finished), patch("fixes.time.sleep") as sleep:
            sleep.side_effect = lambda _: self.assertEqual(fixer.on_progress.call_args.kwargs["devin_session_url"], "https://app.devin.ai/sessions/test")
            result = fixer.generate([], [], "/unused", SITE, personas=[])
        self.assertEqual(result["source"], "devin")
        self.assertEqual(fixer.on_progress.call_args.kwargs["devin_status"], "finished")

    def test_fallback_announces_local_provider(self):
        primary = Mock(source="devin")
        primary.generate.side_effect = RuntimeError("unavailable")
        fallback = Mock(source="rules")
        fallback.generate.return_value = {"source": "rules", "fixes": []}
        fixer = FallbackFixer(primary, fallback)
        fixer.on_progress = Mock()
        result = fixer.generate([], [], "/unused", SITE)
        self.assertEqual(result["fallback_reason"], "RuntimeError")
        self.assertEqual(fixer.on_progress.call_args.kwargs["fix_provider"], "local")

    def test_patch_script_scopes_fixes_to_their_page(self):
        script = patch_script([{"id": "x", "page_url": "http://site.test/cart", "css": ".a{color:red}", "js": "window.ran = 1;"}])
        self.assertIn('path: "/cart"', script)
        self.assertIn(".a{color:red}", script)
        self.assertIn("__auditPagePath", script)  # snapshots re-rendered from a local file still match the page

    def test_canned_fixer_only_fixes_what_failed_on_its_own_site(self):
        fixer = CannedFixer(DEMO)
        got = fixer.generate([], rows(baseline=1.0, keyboard_only=0.0, tremor=1.0), "/unused", SITE)
        self.assertEqual([f["id"] for f in got["fixes"]], ["checkout-keyboard"])
        other_site = fixer.generate([], rows(keyboard_only=0.0), "/unused", {"start_url": "https://customer.example/"})
        self.assertEqual(other_site["fixes"], [])

    def test_merged_patch_keeps_earlier_fixes(self):
        fixer = CannedFixer(DEMO)
        first = fixer.generate([], rows(keyboard_only=0.0), "/unused", SITE)
        second = fixer.generate([], rows(keyboard_only=0.0, color_blind=0.0), "/unused", SITE, previous=first)
        self.assertEqual([f["id"] for f in second["fixes"]], ["stock-text-labels"])  # only what is new
        self.assertEqual({f["id"] for f in merge_patches(first, second)["fixes"]}, {"checkout-keyboard", "stock-text-labels"})

    def test_crop_starts_slider_at_the_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            before, after = Path(tmp, "b.png"), Path(tmp, "a.png")
            Image.new("RGB", (1280, 800), "white").save(before)
            img = Image.new("RGB", (1280, 800), "white")
            ImageDraw.Draw(img).rectangle((900, 300, 1000, 340), fill="navy")
            img.save(after)
            split = crop_to_content(before, after)
            with Image.open(before) as b, Image.open(after) as a:
                self.assertEqual(b.size, a.size)
                self.assertLess(a.width, 1280)
            self.assertTrue(0 < split < 100)


class PreviewTests(unittest.TestCase):
    def test_focus_fixes_name_the_controls_to_focus(self):
        css = ":focus-visible { outline: 3px solid } .day:focus-visible { x: 1 } .stepper:focus-within, .stepper button:focus-visible { y: 2 }"
        self.assertEqual(focus_selectors([{"css": css}]), [".day", ".stepper", ".stepper button"])
        self.assertIsNone(focus_selectors([{"css": ".total { color: #000 }"}]))

    def test_page_changes_read_as_short_lines(self):
        changes = ([{"kind": "attr", "el": "div.day.is-open", "name": "role", "value": "button"}] * 24
                   + [{"kind": "attr", "el": "div.day", "name": "aria-label", "value": f"Tuesday {i} December"} for i in range(24)]
                   + [{"kind": "attr", "el": "div.grid", "name": "role", "value": "group"}]
                   + [{"kind": "add", "el": "span.status", "text": "full"}] * 3)
        self.assertEqual(summarize_changes(changes), [
            'role="button" on 24 div.day', 'aria-label on 24 div.day, e.g. "Tuesday 0 December"', "adds 3 span.status"])


class ReevaluateApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(store, "DATA", Path(self.temp.name))
        self.storage.start()
        login_attempts.clear()
        self.ctx = TestClient(app)
        self.client = self.ctx.__enter__()
        r = self.client.post("/api/auth/register", json={"name": "A", "email": "a@example.test", "password": "local-test-password"})
        self.assertEqual(r.status_code, 200, r.text)

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        self.storage.stop()
        self.temp.cleanup()

    def launch(self, path, body):
        with patch("app.main.manager.start", return_value=True), patch("chrome.chrome_binary", return_value="chrome"), \
                patch.dict("os.environ", {"HAI_API_KEY": "fixture-key"}):
            return self.client.post(path, json=body)

    def finished_audit_with_fixes(self):
        body = {"name": "Checkout", "url": "http://127.0.0.1:8765/", "personas": ["keyboard_only"], "repetitions": 1,
                "tasks": [{"title": "Checkout", "goal": "Find a product and report the checkout total."}],
                "credentials": {"username": "fixture-user", "password": "fixture-site-secret"}}
        audit = self.launch("/api/audits", body).json()
        self.assertTrue(audit["config"]["uses_login"])
        self.assertNotIn("fixture-site-secret", json.dumps(audit))
        store.update(audit["id"], status="completed", fixes={"patch": json.loads(DEMO.read_text()), "comparisons": []})
        return audit

    def test_reevaluation_requires_fixes_and_manual_credentials_again(self):
        audit = self.finished_audit_with_fixes()
        missing = self.launch(f"/api/audits/{audit['id']}/reevaluate", {})
        self.assertEqual(missing.status_code, 422, missing.text)
        ok = self.launch(f"/api/audits/{audit['id']}/reevaluate",
                         {"credentials": {"username": "fixture-user", "password": "fixture-site-secret"}})
        self.assertEqual(ok.status_code, 201, ok.text)
        child = ok.json()
        self.assertEqual(child["parent_id"], audit["id"])
        self.assertEqual(len(child["config"]["patch"]["fixes"]), 5)
        detail = self.client.get(f"/api/audits/{child['id']}").json()
        self.assertEqual(detail["parent"]["id"], audit["id"])
        parent = self.client.get(f"/api/audits/{audit['id']}").json()
        self.assertEqual([r["id"] for r in parent["reevaluations"]], [child["id"]])

    def test_audit_without_fixes_cannot_be_reevaluated(self):
        body = {"name": "Plain", "url": "http://127.0.0.1:8765/", "personas": ["baseline"], "repetitions": 1,
                "tasks": [{"title": "Read", "goal": "Read the main heading of the page."}]}
        audit = self.launch("/api/audits", body).json()
        store.update(audit["id"], status="completed")
        r = self.launch(f"/api/audits/{audit['id']}/reevaluate", {})
        self.assertEqual(r.status_code, 409, r.text)



class ExportTests(unittest.TestCase):
    def test_report_builds_without_images_or_scores(self):
        from export_report import build
        audit = {"name": "Fixture <audit>", "url": "http://127.0.0.1:8770/", "config": {"tasks": [{"id": "t", "title": "Book"}],
                 "personas": ["baseline", "keyboard_only"]}, "results": [
                 {"persona": "keyboard_only", "task": "t", "run": 0, "tag": "t__keyboard_only__0", "success": False,
                  "report": {"obstacles": [{"severity": "blocking", "where": "Workshop", "what": "Dates are not focusable"}]},
                  "replay": {"frames": [{"file": "frames/missing/000.jpg"}], "steps": [{"thought": "Tab", "frame": 0, "action": {"kind": "keys", "label": "Press Tab"}}]}}],
                 "scores": {"score": 50, "grade": "D", "perspectives": {"keyboard_only": {"completion": 0, "grade": "F", "score": 0, "barriers": 1}}}}
        with tempfile.TemporaryDirectory() as tmp:
            page = build(audit, tmp)
        self.assertIn("Fixture &lt;audit&gt;", page)  # escaped
        self.assertIn("Dates are not focusable", page)
        self.assertIn("data-replay", page)


class FallbackTests(unittest.TestCase):
    def test_devin_failure_falls_back_to_local_fixes(self):
        from fixes import FallbackFixer

        class Broken:
            source = "devin"
            def generate(self, *a, **k):
                raise TimeoutError("no answer")

        fixer = FallbackFixer(Broken(), CannedFixer(DEMO))
        out = fixer.generate([], rows(keyboard_only=0.0), "/unused", SITE)
        self.assertEqual([f["id"] for f in out["fixes"]], ["checkout-keyboard"])
        self.assertEqual(out["fallback_reason"], "TimeoutError")
        self.assertEqual(fixer.source, "canned")


if __name__ == "__main__":
    unittest.main()

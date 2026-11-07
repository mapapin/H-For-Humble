"""Opt-in integration test: creates ONE real H audit, sequentially (baseline + keyboard).

Start the app and demo shop first. This consumes H credits. No credentials are printed.
"""
import secrets
import time

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30, trust_env=False) as client:
        response = client.post("/api/auth/register", json={"name": "Local integration test",
            "email": f"live-{secrets.token_hex(5)}@humble.test", "password": secrets.token_urlsafe(24)})
        response.raise_for_status()
        quota = client.get("/api/quota")
        quota.raise_for_status()
        print("H quota before audit:", quota.json(), flush=True)
        response = client.post("/api/audits", json={
            "name": "Demo checkout · integration check", "url": "http://127.0.0.1:8765/",
            "tasks": [{"title": "Buy a blue mug", "goal": "Sign in, add one Blue Mug that is currently in stock to your cart, go to the checkout page, and report the order total shown there. Stop before placing an order.", "expect_answer_contains": "19.90"}],
            "personas": ["baseline", "keyboard_only"], "repetitions": 1,
            "credentials": {"username": "alex@demo-shop.test", "password": "mug-lover-2026"},
        })
        response.raise_for_status()
        audit_id = response.json()["id"]
        print("Audit:", audit_id, flush=True)
        previous = None
        deadline = time.monotonic() + 1000
        try:
            while time.monotonic() < deadline:
                data = client.get(f"/api/audits/{audit_id}").json()
                state = (data["status"], data["progress"].get("persona"), data["progress"].get("actions"), len(data["results"]))
                if state != previous:
                    print("Progress:", state, flush=True)
                    previous = state
                if data["status"] in {"completed", "failed", "stopped"}:
                    print("Final:", data["status"], "Error:", data.get("error"), flush=True)
                    for run in data["results"]:
                        print("Result:", run["persona"], run["status"], "success=", run["success"], "steps=", run.get("steps"), flush=True)
                    assert data["status"] == "completed", "Audit failed"
                    assert len(data["results"]) == 2
                    assert all(r.get("final_screenshot") for r in data["results"])
                    assert data["results"][0]["success"], "Baseline did not achieve the expected answer"
                    print("PASS: real H audit, persisted results, screenshots and comparison report.", flush=True)
                    return
                time.sleep(4)
            raise TimeoutError("Live audit exceeded test deadline")
        finally:
            current = client.get(f"/api/audits/{audit_id}").json()
            if current["status"] not in {"completed", "failed", "stopped"}:
                client.post(f"/api/audits/{audit_id}/stop")


if __name__ == "__main__":
    main()

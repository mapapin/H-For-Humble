"""Local browser QA. Does not launch H runs. Requires Chrome + Selenium from requirements.txt."""
import json
import os
import secrets
import sys
import time
import uuid
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "poc"))
OUT = ROOT / "data" / "qa"
OUT.mkdir(parents=True, exist_ok=True)
options = webdriver.ChromeOptions()
os.environ["SE_SKIP_DRIVER_IN_PATH"] = "true"
options.add_argument("--headless=new")
options.add_argument("--window-size=1440,1100")
driver = webdriver.Chrome(options=options)
# UI checks use a quota fixture, never the H API.
driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': "const originalFetch=window.fetch.bind(window); window.fetch=(url,opts)=>String(url)==='/api/quota'?Promise.resolve(new Response(JSON.stringify({limit:3,active:0,available:3}),{status:200,headers:{'Content-Type':'application/json'}})):originalFetch(url,opts);"})
wait = WebDriverWait(driver, 20)


def click(text):
    wait.until(EC.element_to_be_clickable((By.XPATH, f"//button[normalize-space()='{text}' or (@role='tab' and starts-with(normalize-space(),'{text}'))]"))).click()


def accessibility(label):
    driver.execute_script((ROOT / "node_modules/axe-core/axe.min.js").read_text())
    result = driver.execute_async_script("const done=arguments[arguments.length-1]; axe.run({runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}}).then(r=>done(r.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))));")
    (OUT / f"accessibility-{label}.json").write_text(json.dumps(result, indent=2))
    serious = [v for v in result if v["impact"] in {"serious", "critical"}]
    if serious:
        print("Accessibility findings:", label, json.dumps(serious), flush=True)
    return serious


def seed_interface_fixture():
    """Only this newly registered QA account gets clearly labelled fixture data. No H calls."""
    from app import store
    from audit import summarize

    user = driver.execute_async_script("const done=arguments[0]; fetch('/api/auth/me').then(r=>r.json()).then(done)")
    assert user["email"].startswith("review-") and user["email"].endswith("@humble.test")
    audit_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    config = {"tasks": [{"id": "journey-1", "title": "Find a mug and reach checkout", "goal": "UI test fixture, not a real H audit."}, {"id": "journey-2", "title": "Read delivery options", "goal": "UI test fixture for a second journey."}],
              "personas": ["baseline", "low_vision", "color_blind", "tremor", "keyboard_only"], "repetitions": 2}
    records = []
    for task in config["tasks"]:
        for i, persona in enumerate(config["personas"]):
            for run in range(config["repetitions"]):
                success = persona in {"baseline", "tremor"}
                tag = f"{task['id']}__{persona}__{run}"
                records.append({"tag": tag, "persona": persona, "task": task["id"], "run": run,
                    "status": "completed", "success": success, "steps": [8, 22, 12, 10, 19][i] + run,
                    "duration_s": 40 + 10 * i, "agent_duration_s": 30 + 10 * i,
                    "final_screenshot": f"{tag}.png", "report": {"completed": success,
                        "summary": "Interface fixture, not a real H observation. This sample tests the report layout.",
                        "answer": "Example answer: €19.90" if success else "",
                        "obstacles": [] if success else [{"where": "Example checkout page", "what": "Example barrier for interface testing: the checkout action cannot be reached.", "severity": "blocking"}]}})
    entries = driver.execute_async_script("const done=arguments[0];fetch('/api/vault').then(r=>r.json()).then(done)")
    config["vault_id"] = entries[0]["id"]
    config["vault_name"] = entries[0]["name"]
    with store.connect() as db:
        db.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (project_id, user["id"], "UI fixture website", "http://127.0.0.1:8765/", time.time()))
        db.execute("INSERT INTO audits(id,user_id,project_id,name,url,status,created,config,results,summary,progress,finished) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (audit_id, user["id"], project_id, "UI test fixture · checkout audit", "http://127.0.0.1:8765/", "completed", time.time(), json.dumps(config), json.dumps(records), json.dumps(summarize(records)), '{"completed":20}', time.time()))
    folder = store.DATA / "audits" / audit_id
    folder.mkdir(parents=True)
    for record in records:
        (folder / record["final_screenshot"]).write_bytes((OUT / "sign-in.png").read_bytes())
    store.event(audit_id, "completed", "UI fixture only. No real H session was created.")
    return audit_id


try:
    driver.get("http://127.0.0.1:8000")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".auth-form")))
    findings = accessibility("sign-in")
    driver.save_screenshot(str(OUT / "sign-in.png"))
    click("Create an account")
    driver.find_element(By.NAME, "name").send_keys("Design review")
    driver.find_element(By.NAME, "email").send_keys(f"review-{secrets.token_hex(4)}@humble.test")
    driver.find_element(By.NAME, "password").send_keys(secrets.token_urlsafe(16))
    click("Create your workspace")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".feature-card")))
    time.sleep(1)
    driver.save_screenshot(str(OUT / "overview-desktop.png"))
    findings += accessibility("overview")
    driver.get("http://127.0.0.1:8000/#vault")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".vault-intro")))
    click("Add credentials")
    fields = driver.find_elements(By.CSS_SELECTOR, ".vault-editor input")
    for field, value in zip(fields, ["UI fixture login", "http://127.0.0.1:8765", "qa@fixture.test", "browser-fixture-password"]):
        field.send_keys(value)
    click("Save to vault")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".vault-card")))
    driver.save_screenshot(str(OUT / "vault-desktop.png"))
    findings += accessibility("vault")
    driver.get("http://127.0.0.1:8000/#overview")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".page-heading")))
    click("New audit")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".modal")))
    fields = driver.find_elements(By.CSS_SELECTOR, ".wizard-content input")
    fields[0].send_keys("Checkout accessibility")
    fields[1].send_keys("http://127.0.0.1:8765/")
    click("Use credential vault")
    dropdown = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".vault-link-panel select")))
    from selenium.webdriver.support.ui import Select
    wait.until(lambda d: len(dropdown.find_elements(By.TAG_NAME, "option")) == 2)
    Select(dropdown).select_by_visible_text("UI fixture login")
    click("Continue")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".journey-form")))
    driver.find_element(By.CSS_SELECTOR, ".journey-form input").send_keys("Find a mug")
    driver.find_element(By.CSS_SELECTOR, ".journey-form textarea").send_keys("Find a blue mug, add it to the cart and report the total. Stop before placing an order.")
    click("Continue")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".persona-picker")))
    time.sleep(2)
    driver.save_screenshot(str(OUT / "audit-configuration.png"))
    findings += accessibility("configuration")
    assert len(driver.find_elements(By.CSS_SELECTOR, ".persona-choice.selected")) == 5
    driver.find_element(By.CSS_SELECTOR, '[aria-label="Close audit configuration"]').click()
    audit_id = seed_interface_fixture()
    driver.get(f"http://127.0.0.1:8000/#audit/{audit_id}")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".result-personas")))
    driver.save_screenshot(str(OUT / "report-desktop.png"))
    findings += accessibility("report")
    assert len(driver.find_elements(By.CSS_SELECTOR, ".result-persona")) == 5
    driver.find_element(By.CSS_SELECTOR, '[aria-label="Inspect Baseline test cases"]').click()
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".case-explorer")))
    groups = driver.find_elements(By.CSS_SELECTOR, ".case-task-group")
    assert len(groups) == 2
    groups[1].find_elements(By.CSS_SELECTOR, ".case-select-button")[1].click()
    wait.until(lambda d: 'journey-2__baseline__1.png' in d.find_element(By.CSS_SELECTOR, ".case-preview-image img").get_attribute('src'))
    driver.save_screenshot(str(OUT / "case-preview-desktop.png"))
    findings += accessibility("case-preview")
    driver.find_element(By.CSS_SELECTOR, '[aria-label="Close case preview"]').click()
    click("Run again")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".vault-link-panel")))
    assert driver.find_element(By.CSS_SELECTOR, '.access-choice button[aria-pressed="true"]').text == 'Use credential vault'
    assert wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.vault-selected strong'))).text == 'UI fixture login'
    driver.find_element(By.CSS_SELECTOR, '[aria-label="Close audit configuration"]').click()
    click("Evidence")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".case-evidence-row")))
    findings += accessibility("evidence")
    click("Activity")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".event-item")))
    exported = driver.execute_async_script(f"const done=arguments[0];fetch('/api/audits/{audit_id}/export').then(r=>r.json()).then(done)")
    assert exported["id"] == audit_id and len(exported["results"]) == 20
    driver.get("http://127.0.0.1:8000/#overview")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".stat-grid")))
    driver.set_window_size(390, 844)
    driver.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True})
    time.sleep(1)
    driver.save_screenshot(str(OUT / "overview-mobile.png"))
    assert driver.execute_script("return document.documentElement.scrollWidth <= window.innerWidth"), "Mobile layout overflows"
    driver.get(f"http://127.0.0.1:8000/#audit/{audit_id}")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".result-personas")))
    driver.save_screenshot(str(OUT / "report-mobile.png"))
    assert driver.execute_script("return document.documentElement.scrollWidth <= window.innerWidth"), "Report layout overflows"
    driver.get("http://127.0.0.1:8000/#history")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".search-box")))
    driver.find_element(By.CSS_SELECTOR, ".search-box input").send_keys("no matching audit")
    wait.until(EC.text_to_be_present_in_element((By.CSS_SELECTOR, ".empty-state h3"), "No matching audits"))
    driver.get("http://127.0.0.1:8000/#settings")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".settings-grid")))
    findings += accessibility("settings")
    click("Sign out of this account")
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".auth-form")))
    errors = [entry for entry in driver.get_log("browser") if entry["level"] == "SEVERE" and "401" not in entry["message"]]
    assert not errors, json.dumps(errors)
    assert not findings, "Accessibility checks found serious or critical issues; see data/qa/accessibility-*.json"
    print("PASS: readable UI, vault creation/reuse, multi-journey case previews, reports, export, mobile and WCAG checks. Report fixture only; no H runs.")
    print("Screenshots:", OUT)
finally:
    driver.quit()

import json

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-owner-token-0123456789abcdef"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def authed(client):
    r = client.post("/api/login", json={"token": TOKEN})
    assert r.status_code == 200
    return client


def test_api_requires_owner(client):
    for path in ("/api/state", "/api/models", "/api/projects", "/api/jobs", "/api/budget", "/api/chat"):
        assert client.get(path).status_code == 401, path
    assert client.get("/api/health").status_code == 200


def test_login_wrong_token_and_throttle(client):
    assert client.post("/api/login", json={"token": "nope"}).status_code == 401
    for _ in range(10):
        client.post("/api/login", json={"token": "nope"})
    assert client.post("/api/login", json={"token": TOKEN}).status_code == 429
    from app import main
    main.throttle.fails.clear()


def test_bearer_token_works(client):
    assert client.get("/api/state", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert client.get("/api/state", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_cookie_writes_require_same_origin(authed):
    assert authed.post("/api/projects", json={"instruction": "30 second podcast"}).status_code == 403
    assert authed.post("/api/projects", json={"instruction": "30 second podcast"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert authed.post("/api/projects", json={"instruction": "30 second podcast"}, headers=ORIGIN).status_code == 200


def test_no_secret_in_state(authed):
    body = authed.get("/api/state").text
    assert TOKEN not in body and "owner_token" not in body


def test_free_30s_project_preflight_ok_and_starts(authed):
    r = authed.post("/api/chat", json={"text": "Create a 30-second podcast with Matt and Chloe about neighbourhood events, three camera angles, captions, and my group name at the bottom left."}, headers=ORIGIN)
    assert r.status_code == 200
    prop = r.json()["proposal"]
    assert prop["preflight"]["errors"] == []
    assert prop["project"]["spec"]["duration_s"] == 30
    assert prop["project"]["spec"]["group_name"] == "Durham & Clarington Community REAL TALK"
    assert prop["estimate"]["typical_usd"] == 0
    pid = prop["project"]["id"]
    s = authed.post(f"/api/projects/{pid}/start", json={}, headers=ORIGIN)
    assert s.status_code == 200 and s.json()["job"]["status"] == "queued" and not s.json()["needs_approval"]
    assert authed.post(f"/api/projects/{pid}/start", json={}, headers=ORIGIN).status_code == 409  # one active job


def test_20_minute_brief_is_refused_with_clear_reasons_not_faked(authed):
    r = authed.post("/api/chat", json={"text": "Create a 20-minute realistic podcast with Matt and Chloe, lip-sync, body movement, three camera angles"}, headers=ORIGIN).json()
    pf = r["proposal"]["preflight"]
    assert r["proposal"]["project"]["spec"]["duration_s"] == 1200
    assert any("template writer" in e for e in pf["errors"])
    pid = r["proposal"]["project"]["id"]
    assert authed.post(f"/api/projects/{pid}/start", json={}, headers=ORIGIN).status_code == 422
    assert any("realistic" in n.lower() or "photoreal" in n.lower() for n in r["proposal"]["notes"])


def test_cloud_visuals_blocked_until_cloud_and_adapter_exist(authed):
    p = authed.post("/api/projects", json={"instruction": "30 second podcast", "spec": {"visuals": "infinitetalk"}}, headers=ORIGIN).json()
    errs = p["preflight"]["errors"]
    assert any("placeholder" in e for e in errs) and any("cloud" in e for e in errs)


def test_blocked_license_model_refused(authed):
    p = authed.post("/api/projects", json={"instruction": "30 second podcast", "spec": {"tts": "f5-tts"}}, headers=ORIGIN).json()
    assert any("licence" in e for e in p["preflight"]["errors"])


def test_media_whitelist_and_traversal(authed):
    p = authed.post("/api/projects", json={"instruction": "30 second podcast"}, headers=ORIGIN).json()["project"]
    for bad in ("..%2f..%2fowner_token", "app.db", "owner_token", "%2e%2e/%2e%2e/app.db"):
        assert authed.get(f"/api/projects/{p['id']}/files/{bad}").status_code in (404, 400)
    assert authed.get(f"/api/projects/{p['id']}/files/episode.mp4").status_code == 404  # not produced yet


def test_models_endpoint_labels_status_and_license(authed):
    ms = {m["id"]: m for m in authed.get("/api/models").json()}
    assert ms["kokoro-onnx-local"]["status"] == "working" and ms["kokoro-onnx-local"]["commercial_ok"]
    assert ms["f5-tts"]["status"] == "blocked" and not ms["f5-tts"]["commercial_ok"]
    assert ms["infinitetalk"]["status"] == "placeholder"
    assert ms["flux1-dev"]["license"]["class"] == "non-commercial"


def test_install_plan_has_no_server_side_third_party_execution(authed):
    plan = authed.get("/api/models/infinitetalk/plan").json()
    assert plan["executes_third_party_code_on_app_server"] is False and plan["blockers"]
    assert authed.post("/api/models/wan2.2-s2v-14b/install", json={}, headers=ORIGIN).status_code == 422  # candidate, no adapter


def test_paid_install_needs_approval(authed):
    j = authed.post("/api/models/chatterbox/install", json={}, headers=ORIGIN).json()
    assert j["status"] == "awaiting_approval"


def test_terminal_disabled_by_default(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/terminal"):
            pass


def test_approve_rejects_over_cap(authed):
    from app import jobq
    j = jobq.enqueue("podcast", None, {}, paid=True)
    r = authed.post(f"/api/jobs/{j['id']}/approve", json={"cost_cap_usd": 500}, headers=ORIGIN)
    assert r.status_code == 422 and "per-job" in r.text
    assert authed.post(f"/api/jobs/{j['id']}/approve", json={"cost_cap_usd": 5}, headers=ORIGIN).status_code == 200


def test_logs_are_redacted_in_api(authed):
    from app import jobq
    j = jobq.enqueue("podcast", None, {})
    jobq.log(j["id"], "calling with hf_abcdefghijklmnopqrstu and Authorization: Bearer abcdefghij12345")
    body = authed.get(f"/api/jobs/{j['id']}/events").text
    assert "hf_abcdefghijklmnopqrstu" not in body and "abcdefghij12345" not in body

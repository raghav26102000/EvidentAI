"""EvidentAI Phase 1 backend tests: health, auth (RS256 + refresh rotation + reuse detection),
RLS multi-tenant isolation, file upload hardening, profiling, sessions, api keys, audit."""
import base64
import io
import json
import os
import time
import uuid
import zipfile

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://emergent-dashboard-14.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


def _uniq(prefix="user"):
    return f"TEST_{prefix}_{uuid.uuid4().hex[:10]}@example.com"


# ---------- Health ----------
class TestHealth:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=15)
        assert r.status_code == 200
        assert r.json().get("status") in ("ok", "healthy", True) or r.json()  # tolerant

    def test_health_db(self):
        r = requests.get(f"{API}/health/db", timeout=15)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_health_clamav(self):
        r = requests.get(f"{API}/health/clamav", timeout=30)
        assert r.status_code == 200
        j = r.json()
        assert j.get("ok") is True
        assert "PONG" in (j.get("info") or "")


# ---------- Auth ----------
@pytest.fixture(scope="module")
def tenant_a():
    email = _uniq("a")
    payload = {"tenant_name": f"TEST_A_{uuid.uuid4().hex[:6]}", "email": email, "password": "S3cure-Pass-42!"}
    s = requests.Session()
    r = s.post(f"{API}/auth/register", json=payload, timeout=30)
    assert r.status_code == 201, r.text
    data = r.json()
    return {"session": s, "email": email, "password": payload["password"], "access": data["access_token"], "user": data.get("user")}


@pytest.fixture(scope="module")
def tenant_b():
    email = _uniq("b")
    payload = {"tenant_name": f"TEST_B_{uuid.uuid4().hex[:6]}", "email": email, "password": "S3cure-Pass-42!"}
    s = requests.Session()
    r = s.post(f"{API}/auth/register", json=payload, timeout=30)
    assert r.status_code == 201, r.text
    data = r.json()
    return {"session": s, "email": email, "password": payload["password"], "access": data["access_token"], "user": data.get("user")}


class TestAuth:
    def test_register_sets_cookie(self, tenant_a):
        cookies = tenant_a["session"].cookies
        assert "evai_rt" in cookies.keys()

    def test_duplicate_register(self, tenant_a):
        payload = {"tenant_name": "TEST_DUP", "email": tenant_a["email"], "password": "S3cure-Pass-42!"}
        r = requests.post(f"{API}/auth/register", json=payload, timeout=30)
        assert r.status_code == 409, r.text

    def test_login_ok(self, tenant_a):
        r = requests.post(f"{API}/auth/login", json={"email": tenant_a["email"], "password": tenant_a["password"]}, timeout=30)
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_login_wrong_password(self, tenant_a):
        r = requests.post(f"{API}/auth/login", json={"email": tenant_a["email"], "password": "wrong-pass-!"}, timeout=30)
        assert r.status_code == 401

    def test_login_unknown_user(self):
        r = requests.post(f"{API}/auth/login", json={"email": "nobody_TEST@example.com", "password": "wrong"}, timeout=30)
        assert r.status_code == 401

    def test_me_with_token(self, tenant_a):
        r = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {tenant_a['access']}"}, timeout=15)
        assert r.status_code == 200
        j = r.json()
        assert "user" in j and "tenant" in j

    def test_me_without_token(self):
        r = requests.get(f"{API}/auth/me", timeout=15)
        assert r.status_code == 401

    def test_jwt_rs256(self, tenant_a):
        token = tenant_a["access"]
        header_b64 = token.split(".")[0] + "=="
        header = json.loads(base64.urlsafe_b64decode(header_b64).decode())
        assert header.get("alg") == "RS256"


class TestRefreshRotationAndReuseDetection:
    """Critical: refresh rotation + family reuse detection."""

    def test_refresh_rotates_and_reuse_detected(self):
        email = _uniq("rot")
        s = requests.Session()
        r = s.post(f"{API}/auth/register", json={"tenant_name": f"TEST_ROT_{uuid.uuid4().hex[:6]}", "email": email, "password": "S3cure-Pass-42!"}, timeout=30)
        assert r.status_code == 201
        cookie1 = s.cookies.get("evai_rt")
        assert cookie1

        # First refresh
        r2 = s.post(f"{API}/auth/refresh", timeout=15)
        assert r2.status_code == 200, r2.text
        assert "access_token" in r2.json()
        cookie2 = s.cookies.get("evai_rt")
        assert cookie2 and cookie2 != cookie1, "refresh cookie must rotate"

        # Replay old cookie1 - should fail
        r_old = requests.post(f"{API}/auth/refresh", cookies={"evai_rt": cookie1}, timeout=15)
        assert r_old.status_code == 401, f"expected 401 on reused refresh, got {r_old.status_code}: {r_old.text}"

        # Family revoked: cookie2 also must now be invalid
        r_new = requests.post(f"{API}/auth/refresh", cookies={"evai_rt": cookie2}, timeout=15)
        assert r_new.status_code == 401, f"expected 401 after family revoke via reuse, got {r_new.status_code}: {r_new.text}"

    def test_logout_revokes_refresh(self):
        email = _uniq("logout")
        s = requests.Session()
        r = s.post(f"{API}/auth/register", json={"tenant_name": f"TEST_LO_{uuid.uuid4().hex[:6]}", "email": email, "password": "S3cure-Pass-42!"}, timeout=30)
        assert r.status_code == 201
        access = r.json()["access_token"]
        cookie = s.cookies.get("evai_rt")
        assert cookie

        rlo = requests.post(f"{API}/auth/logout", headers={"Authorization": f"Bearer {access}"}, cookies={"evai_rt": cookie}, timeout=15)
        assert rlo.status_code in (200, 204)

        rref = requests.post(f"{API}/auth/refresh", cookies={"evai_rt": cookie}, timeout=15)
        assert rref.status_code == 401


# ---------- Datasets / Upload ----------
def _auth(access):
    return {"Authorization": f"Bearer {access}"}


class TestUploadHardening:
    def test_reject_txt_extension(self, tenant_a):
        files = {"file": ("bad.txt", b"hello world", "text/plain")}
        r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=30)
        assert r.status_code == 415, r.text

    def test_reject_png(self, tenant_a):
        files = {"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")}
        r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=30)
        assert r.status_code == 415

    def test_empty_file(self, tenant_a):
        files = {"file": ("empty.csv", b"", "text/csv")}
        r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=30)
        assert r.status_code == 400

    def test_xlsm_rejected(self, tenant_a):
        files = {"file": ("macro.xlsm", b"anything", "application/vnd.ms-excel.sheet.macroEnabled.12")}
        r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=30)
        assert r.status_code == 422
        assert "disallowed_extension" in r.text or "macro" in r.text.lower()

    def test_xlsx_with_vba(self, tenant_a):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/vbaProject.bin", b"FAKE")
            z.writestr("[Content_Types].xml", "<x/>")
        files = {"file": ("macro.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=30)
        assert r.status_code == 422
        assert "vbaProject" in r.text or "blocked_content" in r.text

    def test_csv_formula_injection(self, tenant_a):
        csv = b"name,val\nAlice,=SUM(A1)\nBob,3\n"
        files = {"file": ("evil.csv", csv, "text/csv")}
        r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=30)
        assert r.status_code == 422
        assert "formula_injection" in r.text or "csv_formula" in r.text


@pytest.fixture(scope="module")
def uploaded_dataset(tenant_a):
    csv = (
        "id,name,age,city,score,note\n"
        "1,Alice,30,NYC,88.5,hello\n"
        "2,Bob,25,LA,91.2,\n"
        "3,Carol,,SF,76.0,world\n"
        "4,Dan,40,NYC,,foo\n"
        "5,Eve,35,LA,82.1,bar\n"
    ).encode()
    files = {"file": ("data.csv", csv, "text/csv")}
    r = requests.post(f"{API}/datasets", headers=_auth(tenant_a["access"]), files=files, timeout=60)
    assert r.status_code in (200, 201), r.text
    ds = r.json()
    return ds


class TestDatasetsHappyPath:
    def test_dataset_created(self, uploaded_dataset):
        assert "id" in uploaded_dataset

    def test_list_shows_dataset_ready(self, tenant_a, uploaded_dataset):
        # give a moment for profiling if async
        for _ in range(10):
            r = requests.get(f"{API}/datasets", headers=_auth(tenant_a["access"]), timeout=15)
            assert r.status_code == 200
            items = r.json()
            found = [d for d in items if d["id"] == uploaded_dataset["id"]]
            if found and found[0].get("status") == "ready":
                assert found[0].get("scan_result") == "clean"
                return
            time.sleep(1)
        pytest.fail(f"dataset not ready in time: {items}")

    def test_profile_shape(self, tenant_a, uploaded_dataset):
        r = requests.get(f"{API}/datasets/{uploaded_dataset['id']}/profile", headers=_auth(tenant_a["access"]), timeout=30)
        assert r.status_code == 200, r.text
        prof = r.json()
        # find a numeric column and a text column
        cols = prof.get("columns") or prof.get("profile", {}).get("columns") or []
        assert cols, f"no columns in profile: {prof}"
        num_ok = False
        text_ok = False
        for c in cols:
            stats = c.get("stats") or {}
            if c.get("name") in ("age", "score", "id"):
                if all(k in stats for k in ("min", "max", "mean", "median", "histogram")):
                    assert isinstance(stats["histogram"], list)
                    num_ok = True
            if c.get("name") in ("name", "city", "note"):
                if "min_len" in stats and "max_len" in stats:
                    text_ok = True
        assert num_ok, f"numeric stats incomplete: {cols}"
        assert text_ok, f"text stats incomplete: {cols}"


# ---------- RLS Multi-tenant isolation ----------
class TestRLS:
    def test_tenant_b_cannot_see_tenant_a_datasets(self, tenant_a, tenant_b, uploaded_dataset):
        # list from B
        r = requests.get(f"{API}/datasets", headers=_auth(tenant_b["access"]), timeout=15)
        assert r.status_code == 200
        items = r.json()
        assert not any(d["id"] == uploaded_dataset["id"] for d in items), "RLS breach: tenant B saw tenant A dataset"

    def test_tenant_b_direct_get_returns_404(self, tenant_b, uploaded_dataset):
        r = requests.get(f"{API}/datasets/{uploaded_dataset['id']}", headers=_auth(tenant_b["access"]), timeout=15)
        assert r.status_code == 404


# ---------- Sessions / API keys / Audit ----------
class TestSessionsApiKeysAudit:
    def test_sessions_list(self, tenant_a):
        r = requests.get(f"{API}/sessions", headers=_auth(tenant_a["access"]), timeout=15)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_api_key_create_and_revoke(self, tenant_a):
        r = requests.post(f"{API}/api-keys", headers=_auth(tenant_a["access"]), json={"name": "TEST_key"}, timeout=15)
        assert r.status_code in (200, 201), r.text
        j = r.json()
        assert "plaintext_key" in j and "prefix" in j
        key_id = j.get("id") or j.get("api_key", {}).get("id")

        rl = requests.get(f"{API}/api-keys", headers=_auth(tenant_a["access"]), timeout=15)
        assert rl.status_code == 200
        keys = rl.json()
        assert all("plaintext_key" not in k for k in keys)

        if key_id:
            rd = requests.delete(f"{API}/api-keys/{key_id}", headers=_auth(tenant_a["access"]), timeout=15)
            assert rd.status_code in (200, 204)

    def test_audit_has_events(self, tenant_a):
        r = requests.get(f"{API}/audit", headers=_auth(tenant_a["access"]), timeout=15)
        assert r.status_code == 200
        events = r.json()
        assert isinstance(events, list)
        assert len(events) >= 1

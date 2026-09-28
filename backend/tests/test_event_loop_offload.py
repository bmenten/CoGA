"""Slow CPU work runs in a worker thread, not on the event loop (#527).

The backend runs one uvicorn worker, so anything slow on the event loop stalls every
request. These pin that the bcrypt check at login and the per-gene phenotype scoring run
off the loop thread, and that the signup notification is configured through Settings.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.core.postgres import get_postgres_session
from backend.app.main import app
from backend.app.routers import auth as auth_router
from backend.app.services import monarch_phenotype_score


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture()
def login_client(monkeypatch: pytest.MonkeyPatch):
    original_overrides = dict(app.dependency_overrides)
    app.state.skip_startup_tasks = True

    async def override_get_postgres_session():
        yield _FakeSession()

    app.dependency_overrides[get_postgres_session] = override_get_postgres_session
    with TestClient(app) as client:
        yield client, monkeypatch
    app.dependency_overrides = original_overrides


def test_login_checks_the_password_off_the_event_loop(login_client) -> None:
    client, monkeypatch = login_client
    threads: dict[str, int] = {}

    async def no_throttle(session, *, email, remote_ip, now=None):
        return None

    async def a_user(session, email):
        threads["loop"] = threading.get_ident()
        return {"hashed_password": "stored-hash", "is_active": True}

    async def record(session, *, email, remote_ip, now=None):
        return None

    def checking_verify(password, hashed):
        threads["verify"] = threading.get_ident()
        return False

    monkeypatch.setattr(auth_router, "get_login_throttle_state", no_throttle)
    monkeypatch.setattr(auth_router, "get_auth_user_mapping_by_email", a_user)
    monkeypatch.setattr(auth_router, "record_failed_login", record)
    monkeypatch.setattr(auth_router, "verify_password", checking_verify)

    response = client.post("/api/auth/login", json={"email": "user@example.com", "password": "wrong"})

    assert response.status_code == 400
    assert threads["verify"] != threads["loop"], "bcrypt ran on the event loop thread"


def test_gene_phenotype_scoring_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    threads: dict[str, int] = {}

    async def information_content(session):
        threads["loop"] = threading.get_ident()
        return {"HP:0000001": 1.0, "HP:0000002": 2.0}, 2.0

    async def gene_terms(session, symbols):
        return {"GENE1": {"HP:0000002"}}, {}

    async def ancestors(session, terms):
        return {term: {term} for term in terms}

    def scoring(patient, terms, *, ancestors, ic, max_ic):
        threads["score"] = threading.get_ident()
        return monarch_phenotype_score.GenePhenotypeScore(score=0.5, matched=[])

    monkeypatch.setattr(monarch_phenotype_score, "_load_information_content", information_content)
    monkeypatch.setattr(monarch_phenotype_score, "_gene_phenotype_terms", gene_terms)
    monkeypatch.setattr(monarch_phenotype_score, "_term_ancestors", ancestors)
    monkeypatch.setattr(monarch_phenotype_score, "phenomizer_score", scoring)

    scores = asyncio.run(
        monarch_phenotype_score.score_genes_for_hpo(
            None, gene_symbols=["gene1"], patient_hpo_ids=["HP:0000002"]
        )
    )

    assert set(scores) == {"GENE1"}
    assert threads["score"] != threads["loop"], "phenotype scoring ran on the event loop thread"


class _FakeSmtp:
    sent: list[tuple[str, object]] = []

    def __init__(self, host: str) -> None:
        self.host = host

    def __enter__(self) -> "_FakeSmtp":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def send_message(self, message) -> None:
        _FakeSmtp.sent.append((self.host, message))


def test_signup_notification_goes_to_admin_email_through_smtp_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSmtp.sent = []
    configured = Settings(_env_file=None, APP_ENV="test", ADMIN_EMAIL="lab@example.org", SMTP_HOST="mail.internal")
    monkeypatch.setattr(auth_router, "settings", configured)
    monkeypatch.setattr(auth_router.smtplib, "SMTP", _FakeSmtp)

    auth_router.notify_admin("new.user@example.org")

    assert len(_FakeSmtp.sent) == 1
    host, message = _FakeSmtp.sent[0]
    assert host == "mail.internal"
    assert message["To"] == "lab@example.org"


def test_no_signup_notification_without_a_configured_admin_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSmtp.sent = []
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    # ADMIN_EMAIL left at its default: the seed admin's placeholder, not an address to mail.
    monkeypatch.setattr(auth_router, "settings", Settings(_env_file=None, APP_ENV="test"))
    monkeypatch.setattr(auth_router.smtplib, "SMTP", _FakeSmtp)

    auth_router.notify_admin("new.user@example.org")

    assert _FakeSmtp.sent == []

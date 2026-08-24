"""Tests for the auth helpers (allowlist, admin, rate limit, token guard)."""

import pytest

import auth
from config import Config


def cfg(**kw):
    return Config(**kw)


class TestIsAllowed:
    def test_email_on_allowlist(self):
        c = cfg(allowed_emails="a@x.com, b@x.com")
        assert auth.is_allowed("a@x.com", c)
        assert auth.is_allowed("b@x.com", c)

    def test_allowlist_is_case_insensitive(self):
        c = cfg(allowed_emails="Person@X.com")
        assert auth.is_allowed("person@x.com", c)

    def test_domain_allows_any_member(self):
        c = cfg(allowed_domain="x.com")
        assert auth.is_allowed("anyone@x.com", c)
        assert not auth.is_allowed("anyone@other.com", c)

    def test_domain_written_with_leading_at(self):
        # "@x.com" is the natural way to write it; it must not lock everyone out.
        c = cfg(allowed_domain="@x.com")
        assert auth.is_allowed("anyone@x.com", c)
        assert not auth.is_allowed("anyone@other.com", c)

    def test_outsider_denied_when_no_domain(self):
        c = cfg(allowed_emails="a@x.com")
        assert not auth.is_allowed("intruder@x.com", c)


class TestIsAdmin:
    def test_admin_match(self):
        c = cfg(admin_emails="boss@x.com")
        assert auth.is_admin("boss@x.com", c)
        assert not auth.is_admin("worker@x.com", c)


class TestRateLimit:
    def test_under_limit_passes_then_blocks(self):
        c = cfg(rate_limit_per_min=2)
        email = "rate-test-unique@x.com"
        auth.check_rate_limit(email, c.rate_limit_per_min)
        auth.check_rate_limit(email, c.rate_limit_per_min)
        with pytest.raises(auth.AuthError) as exc:
            auth.check_rate_limit(email, c.rate_limit_per_min)
        assert exc.value.code == 429

    def test_zero_disables_limit(self):
        for _ in range(100):
            auth.check_rate_limit("no-limit@x.com", 0)

    def test_window_rollover_resets_the_count(self, monkeypatch):
        email = "rollover@x.com"
        monkeypatch.setattr(auth.time, "time", lambda: 600.0)  # minute 10
        auth.check_rate_limit(email, 1)
        with pytest.raises(auth.AuthError):
            auth.check_rate_limit(email, 1)
        monkeypatch.setattr(auth.time, "time", lambda: 660.0)  # minute 11
        auth.check_rate_limit(email, 1)  # fresh window, allowed again

    def test_stale_callers_are_evicted(self, monkeypatch):
        monkeypatch.setattr(auth.time, "time", lambda: 600.0)
        for i in range(5):
            auth.check_rate_limit("gone-{}@x.com".format(i), 10)
        monkeypatch.setattr(auth.time, "time", lambda: 660.0)
        auth.check_rate_limit("still-here@x.com", 10)
        # The next window keeps only the caller active in it, not the whole
        # history of everyone who has ever called.
        assert set(auth._rate_buckets) == {"still-here@x.com"}


class TestVerifyCaller:
    def test_missing_token_is_401(self):
        with pytest.raises(auth.AuthError) as exc:
            auth.verify_caller("")
        assert exc.value.code == 401
        with pytest.raises(auth.AuthError):
            auth.verify_caller(None)

    def test_bad_token_is_401(self, monkeypatch):
        from google.oauth2 import id_token

        def boom(*a, **kw):
            raise ValueError("Token signature is invalid")

        monkeypatch.setattr(id_token, "verify_oauth2_token", boom)
        with pytest.raises(auth.AuthError) as exc:
            auth.verify_caller("some-token")
        assert exc.value.code == 401

    def test_cert_fetch_failure_is_503_not_401(self, monkeypatch):
        # We could not check the token; that is our outage, not a bad login.
        from google.auth.exceptions import TransportError
        from google.oauth2 import id_token

        def boom(*a, **kw):
            raise TransportError("connection reset")

        monkeypatch.setattr(id_token, "verify_oauth2_token", boom)
        with pytest.raises(auth.AuthError) as exc:
            auth.verify_caller("some-token")
        assert exc.value.code == 503

    def test_unverified_email_is_rejected(self, monkeypatch):
        from google.oauth2 import id_token

        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **kw: {"email": "a@x.com", "email_verified": False},
        )
        with pytest.raises(auth.AuthError) as exc:
            auth.verify_caller("some-token")
        assert exc.value.code == 401

    def test_verified_email_is_lowercased(self, monkeypatch):
        from google.oauth2 import id_token

        monkeypatch.setattr(
            id_token,
            "verify_oauth2_token",
            lambda *a, **kw: {"email": "Person@X.com", "email_verified": True},
        )
        assert auth.verify_caller("some-token") == "person@x.com"

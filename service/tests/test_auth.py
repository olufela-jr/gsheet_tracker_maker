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


class TestVerifyCaller:
    def test_missing_token_is_401(self):
        with pytest.raises(auth.AuthError) as exc:
            auth.verify_caller("")
        assert exc.value.code == 401
        with pytest.raises(auth.AuthError):
            auth.verify_caller(None)

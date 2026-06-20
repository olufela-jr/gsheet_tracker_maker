"""Caller authentication and authorization for the service.

The relay forwards the caller's Google identity token in the request body. We
verify it here (signature, issuer, expiry) and ignore its audience, which is
what lets any child work without per-child Cloud Run config. Access is then
gated by an allowlist, a per-caller rate limit, and per-spreadsheet ownership.
"""

import threading
import time


class AuthError(Exception):
    """Raised when a caller cannot be authenticated or authorized."""

    def __init__(self, message, code=403):
        self.message = message
        self.code = code
        super().__init__(message)


def verify_caller(token):
    """Verify a Google identity token and return the verified email (lowercased).

    Audience is intentionally not checked: the token comes from one of many
    Apps Script projects, each with its own audience. Signature, issuer, and
    expiry are still enforced, so the identity is trustworthy.
    """
    if not token:
        raise AuthError("Missing identity token", 401)
    # Imported lazily so the module loads (and unit tests run) without the
    # google auth transport and its requests dependency.
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            token, ga_requests.Request(), audience=None, clock_skew_in_seconds=10
        )
    except Exception as exc:  # noqa: BLE001 - any verification failure is a 401
        raise AuthError("Invalid identity token: {}".format(exc), 401)
    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified", False):
        raise AuthError("Token has no verified email", 401)
    return email


def _emails(value):
    return {e.strip().lower() for e in (value or "").split(",") if e.strip()}


def is_allowed(email, cfg):
    """True if the email is on the allowlist or in the allowed domain."""
    if email in _emails(cfg.allowed_emails):
        return True
    domain = (cfg.allowed_domain or "").strip().lower()
    return bool(domain) and email.endswith("@" + domain)


def is_admin(email, cfg):
    """Admins may act on any tracker, not only ones they created."""
    return email in _emails(cfg.admin_emails)


# Per-caller rate limit. Best-effort and per-instance (Cloud Run autoscales);
# a hard cross-instance limit would need shared state such as Firestore.
_rate_lock = threading.Lock()
_rate_buckets = {}  # email -> [window_minute, count]


def check_rate_limit(email, limit_per_min):
    if limit_per_min <= 0:
        return
    window = int(time.time() // 60)
    with _rate_lock:
        bucket = _rate_buckets.get(email)
        if not bucket or bucket[0] != window:
            bucket = [window, 0]
        bucket[1] += 1
        _rate_buckets[email] = bucket
        if bucket[1] > limit_per_min:
            raise AuthError("Rate limit exceeded, try again shortly", 429)

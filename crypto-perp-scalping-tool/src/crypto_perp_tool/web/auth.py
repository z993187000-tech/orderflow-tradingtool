from __future__ import annotations

import base64
import hmac
from collections.abc import Mapping


def required_auth_header() -> str:
    return 'Basic realm="Order Flow Dashboard"'


def is_authorized(headers: Mapping[str, str], password: str | None) -> bool:
    if not password:
        return True

    header = headers.get("Authorization") or headers.get("authorization")
    if not header or not header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(header.removeprefix("Basic ").strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False

    _, _, supplied_password = decoded.partition(":")
    return hmac.compare_digest(supplied_password, password)

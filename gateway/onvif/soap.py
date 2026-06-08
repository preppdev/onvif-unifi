"""Parse incoming ONVIF SOAP: action name, ProfileToken, and WS-Security auth."""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from typing import Optional
from xml.etree import ElementTree as ET

log = logging.getLogger("onvif.soap")

_WSSE_PASSWORD_DIGEST = (
    "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest"
)


def action_name(body: bytes) -> Optional[str]:
    """Local-name of the first element inside <s:Body> — the ONVIF operation."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    body_el = root.find("{http://www.w3.org/2003/05/soap-envelope}Body")
    if body_el is None or len(body_el) == 0:
        return None
    tag = body_el[0].tag
    return tag.split("}", 1)[1] if "}" in tag else tag


def profile_token(body: bytes) -> Optional[str]:
    """Extract <ProfileToken> from a GetStreamUri/GetSnapshotUri request."""
    m = re.search(rb"<[\w:]*ProfileToken>\s*([^<]+?)\s*</[\w:]*ProfileToken>", body)
    return m.group(1).decode().strip() if m else None


def config_token(body: bytes) -> Optional[str]:
    """Extract <ConfigurationToken> from a Get*Configuration request."""
    m = re.search(rb"<[\w:]*ConfigurationToken>\s*([^<]+?)\s*</[\w:]*ConfigurationToken>", body)
    return m.group(1).decode().strip() if m else None


def stream_protocol(body: bytes) -> str:
    m = re.search(rb"<[\w:]*Protocol>\s*([^<]+?)\s*</[\w:]*Protocol>", body)
    return m.group(1).decode().strip() if m else "RTSP"


class _UsernameToken:
    __slots__ = ("username", "digest", "nonce", "created", "password_text")

    def __init__(self, username, digest, nonce, created, password_text):
        self.username = username
        self.digest = digest
        self.nonce = nonce
        self.created = created
        self.password_text = password_text


def _parse_token(body: bytes) -> Optional[_UsernameToken]:
    text = body.decode("utf-8", "replace")
    user = re.search(r"<[\w:]*Username>\s*([^<]*?)\s*</[\w:]*Username>", text)
    if not user:
        return None
    pwd = re.search(r'<[\w:]*Password(?:\s+Type="([^"]*)")?>\s*([^<]*?)\s*</[\w:]*Password>', text)
    nonce = re.search(r"<[\w:]*Nonce[^>]*>\s*([^<]*?)\s*</[\w:]*Nonce>", text)
    created = re.search(r"<[\w:]*Created[^>]*>\s*([^<]*?)\s*</[\w:]*Created>", text)
    pwd_type = pwd.group(1) if pwd else ""
    pwd_val = pwd.group(2) if pwd else ""
    is_digest = pwd_type == "" or "PasswordDigest" in pwd_type
    return _UsernameToken(
        username=user.group(1),
        digest=pwd_val if is_digest else None,
        nonce=nonce.group(1) if nonce else "",
        created=created.group(1) if created else "",
        password_text=None if is_digest else pwd_val,
    )


def verify_auth(body: bytes, username: str, password: str) -> bool:
    """Verify a WS-UsernameToken (PasswordDigest or PasswordText) against creds.

    Digest = Base64( SHA1( base64decode(nonce) + created + password ) ).
    """
    token = _parse_token(body)
    if token is None:
        return False
    if token.username != username:
        return False
    if token.password_text is not None:
        return token.password_text == password
    if not token.digest:
        return False
    try:
        nonce_raw = base64.b64decode(token.nonce)
    except (ValueError, TypeError):
        nonce_raw = token.nonce.encode()
    expected = base64.b64encode(
        hashlib.sha1(nonce_raw + token.created.encode() + password.encode()).digest()
    ).decode()
    return expected == token.digest

"""Pro license: OFFLINE ed25519 verification (RFC 8032), stdlib only - no
network, no new dependencies, works unchanged under uvx.

License string format:  base64url(payload-json) + "." + base64url(64-byte sig)
Payload: compact JSON, e.g. {"exp":<epoch sec>,"note":"buyer@x","tier":"pro"}

The verification public key ships embedded below; the signing seed NEVER
ships in this repo or the skill zip. ed25519 sign/verify is a direct port
of the reference implementation in RFC 8032 section 6 (IETF illustration
code) so both directions are covered by the RFC's own test vectors.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time

# ---- ed25519 (RFC 8032 reference port) ----

p = 2**255 - 19


def modp_inv(x):
    return pow(x, p - 2, p)


d = -121665 * modp_inv(121666) % p
q = 2**252 + 27742317777372353535851937790883648493


def sha512_modq(s):
    return int.from_bytes(hashlib.sha512(s).digest(), "little") % q


def point_add(P, Q):
    A, B = (P[1] - P[0]) * (Q[1] - Q[0]) % p, (P[1] + P[0]) * (Q[1] + Q[0]) % p
    C, D = 2 * P[3] * Q[3] * d % p, 2 * P[2] * Q[2] % p
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F, G * H, F * G, E * H)


def point_mul(s, P):
    Q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            Q = point_add(Q, P)
        P = point_add(P, P)
        s >>= 1
    return Q


def point_equal(P, Q):
    if (P[0] * Q[2] - Q[0] * P[2]) % p != 0:
        return False
    if (P[1] * Q[2] - Q[1] * P[2]) % p != 0:
        return False
    return True


modp_sqrt_m1 = pow(2, (p - 1) // 4, p)


def recover_x(y, sign):
    if y >= p:
        return None
    x2 = (y * y - 1) * modp_inv(d * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (p + 3) // 8, p)
    if (x * x - x2) % p != 0:
        x = x * modp_sqrt_m1 % p
    if (x * x - x2) % p != 0:
        return None
    if (x & 1) != sign:
        x = p - x
    return x


g_y = 4 * modp_inv(5) % p
g_x = recover_x(g_y, 0)
G = (g_x, g_y, 1, g_x * g_y % p)


def point_compress(P):
    zinv = modp_inv(P[2])
    x = P[0] * zinv % p
    y = P[1] * zinv % p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def point_decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % p)


def secret_expand(secret):
    if len(secret) != 32:
        raise ValueError("ed25519 seed must be 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return (a, h[32:])


def secret_to_public(seed: bytes) -> bytes:
    (a, _) = secret_expand(seed)
    return point_compress(point_mul(a, G))


def ed25519_sign(seed: bytes, msg: bytes) -> bytes:
    a, prefix = secret_expand(seed)
    A = point_compress(point_mul(a, G))
    r = sha512_modq(prefix + msg)
    R = point_mul(r, G)
    Rs = point_compress(R)
    h = sha512_modq(Rs + A + msg)
    s = (r + h * a) % q
    return Rs + int.to_bytes(s, 32, "little")


def ed25519_verify(public: bytes, msg: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A = point_decompress(public)
    if A is None:
        return False
    Rs = signature[:32]
    R = point_decompress(Rs)
    if R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= q:
        return False
    h = sha512_modq(Rs + public + msg)
    return point_equal(point_mul(s, G), point_add(R, point_mul(h, A)))


# ---- license format ----

# Issuer public key (ed25519). Seed lives only in the issuer's local
# secret file (license-seed.secret, gitignored) + scripts/issue_license.py.
PUBKEY_HEX = "8190f1f0a6aa7951bf979d63bda4486be8726873c35ae3f6a64ab5ba5d4ec81a"
PUBKEY = bytes.fromhex(PUBKEY_HEX)  # issuer public key; seed never ships

FREE_MAX_REPOS = 5  # free tier fleet cap; CBM_LICENSE unlocks unlimited


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def issue(payload: dict, seed: bytes) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _b64e(body) + "." + _b64e(ed25519_sign(seed, body))


def check(lic: str, pubkey: bytes, now: float | None = None) -> dict | None:
    """Parse + verify a license string. Payload dict on success, None on ANY
    failure (format, signature, expiry) - callers fall back to the free tier."""
    try:
        body, sig = lic.strip().rsplit(".", 1)
        raw = _b64d(body)
        sig_raw = _b64d(sig)
        # canonical base64url only (RFC 4648): encodings that re-encode to a
        # different string carry dirty trailing bits -> reject
        if _b64e(raw) != body or _b64e(sig_raw) != sig:
            return None
        if not ed25519_verify(pubkey, raw, sig_raw):
            return None
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("tier") != "pro":
            return None
        exp = payload.get("exp")
        if exp is not None and (now if now is not None else time.time()) >= exp:
            return None
        return payload
    except Exception:
        return None


def load_env(now: float | None = None) -> dict | None:
    lic = os.environ.get("CBM_LICENSE")
    return check(lic, PUBKEY, now) if lic else None


def is_pro(now: float | None = None) -> bool:
    return load_env(now) is not None


def notice(path_count: int) -> str:
    if is_pro():
        return ""
    extra = max(0, path_count - FREE_MAX_REPOS)
    return (
        f"cbm-memory: free tier indexes {FREE_MAX_REPOS} repo(s) per server "
        f"({extra} skipped); set CBM_LICENSE for fleet mode "
        f"(codebase-memory-mcp Pro)"
    )

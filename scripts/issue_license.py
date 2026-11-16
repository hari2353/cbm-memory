#!/usr/bin/env python3
"""Issue a cbm-memory Pro license (offline ed25519, RFC 8032).

The signing seed lives ONLY in license-seed.secret (gitignored, chmod 600)
next to the repo root. Never commit it, never ship it in the skill zip.

Usage:
  python scripts/issue_license.py --gen
      Mint a new issuer keypair: writes license-seed.secret, prints the
      public key to embed in cbm_memory.licensing.PUBKEY_HEX (one time only).

  python scripts/issue_license.py note=alice@x [exp=1900000000] [...]
      Print a signed license string for CBM_LICENSE. Keys become payload
      fields (tier defaults to "pro"; exp is epoch seconds, optional).
"""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from cbm_memory import licensing  # noqa: E402

SEED_FILE = pathlib.Path(__file__).resolve().parents[1] / "license-seed.secret"


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--gen":
        seed = os.urandom(32)
        SEED_FILE.write_text(seed.hex())
        SEED_FILE.chmod(0o600)
        print(f"issuer pubkey (embed in licensing.PUBKEY_HEX):\n"
              f"  {licensing.secret_to_public(seed).hex()}")
        print(f"wrote {SEED_FILE} (chmod 600; keep it OUT of git and zips)")
        return

    if not SEED_FILE.exists():
        sys.exit(f"missing {SEED_FILE} - run: python scripts/issue_license.py --gen")
    seed = bytes.fromhex(SEED_FILE.read_text().strip())
    payload = {"tier": "pro"}
    for a in args:
        k, _, v = a.partition("=")
        payload[k] = int(v) if k == "exp" else v
    print(licensing.issue(payload, seed))


if __name__ == "__main__":
    main()

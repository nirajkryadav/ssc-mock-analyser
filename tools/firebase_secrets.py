"""Turn a downloaded Firebase service-account key (.json) into Streamlit secrets (TOML).

Streamlit's Secrets box takes TOML, not JSON, so pasting the key file as-is is
rejected as "invalid format". This writes the [firebase] section for you.

    python3 tools/firebase_secrets.py ~/Downloads/<key>.json --copy
        copies the section to the clipboard (macOS) without printing the key

    python3 tools/firebase_secrets.py ~/Downloads/<key>.json --write
        saves it as .streamlit/secrets.toml for running the app locally
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ("type", "project_id", "private_key", "client_email", "token_uri")


def to_toml(key: dict) -> str:
    missing = [k for k in REQUIRED if not key.get(k)]
    if missing:
        raise ValueError(f"not a service-account key, missing: {', '.join(missing)}")
    # A JSON string literal is also a valid TOML basic string (same escapes),
    # so the private key's newlines survive as \n.
    lines = ["[firebase]"] + [f"{k} = {json.dumps(v)}" for k, v in key.items()
                              if isinstance(v, str)]
    text = "\n".join(lines) + "\n"
    if tomllib.loads(text)["firebase"] != {k: v for k, v in key.items() if isinstance(v, str)}:
        raise ValueError("round-trip check failed")  # never expected
    return text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("key_file", type=Path)
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--copy", action="store_true", help="copy to the clipboard (macOS)")
    out.add_argument("--write", action="store_true", help="write .streamlit/secrets.toml")
    out.add_argument("--print", action="store_true", help="print to the terminal")
    args = ap.parse_args(argv)

    text = to_toml(json.loads(args.key_file.expanduser().read_text("utf-8")))
    if args.copy:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        print("Copied the [firebase] section to the clipboard. "
              "Paste it into the app's Settings -> Secrets and click Save.")
    elif args.write:
        dest = ROOT / ".streamlit" / "secrets.toml"
        dest.parent.mkdir(exist_ok=True)
        dest.write_text(text, "utf-8")
        dest.chmod(0o600)
        print(f"Wrote {dest.relative_to(ROOT)} (git-ignored).")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

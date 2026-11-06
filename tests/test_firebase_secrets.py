import importlib.util
import tomllib
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "firebase_secrets", Path(__file__).resolve().parents[1] / "tools" / "firebase_secrets.py")
fs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fs)

FAKE = {
    "type": "service_account",
    "project_id": "demo-project",
    "private_key_id": "abc123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nAAAA/+bbbb==\n-----END PRIVATE KEY-----\n",
    "client_email": "svc@demo-project.iam.gserviceaccount.com",
    "client_id": "1",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/svc%40demo",
    "universe_domain": "googleapis.com",
}


def test_output_is_valid_toml_with_the_same_values():
    parsed = tomllib.loads(fs.to_toml(FAKE))["firebase"]
    assert parsed == FAKE
    assert parsed["private_key"].count("\n") == 3  # real newlines, not "\\n"


def test_rejects_a_file_that_is_not_a_service_account_key():
    with pytest.raises(ValueError, match="private_key"):
        fs.to_toml({"type": "service_account", "project_id": "x"})

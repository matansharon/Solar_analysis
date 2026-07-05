import os
import pytest
from solaranalysis.web import crypto


def test_roundtrip(tmp_path):
    key = crypto.load_or_create_key(str(tmp_path / "secret.key"))
    tok = crypto.encrypt(key, "hunter2")
    assert isinstance(tok, bytes)
    assert tok != b"hunter2"
    assert crypto.decrypt(key, tok) == "hunter2"


def test_key_is_stable_and_file_created(tmp_path):
    kp = tmp_path / "secret.key"
    k1 = crypto.load_or_create_key(str(kp))
    assert kp.exists()
    k2 = crypto.load_or_create_key(str(kp))
    assert k1 == k2  # second call reuses the file


def test_wrong_key_cannot_decrypt(tmp_path):
    k1 = crypto.load_or_create_key(str(tmp_path / "a.key"))
    k2 = crypto.load_or_create_key(str(tmp_path / "b.key"))
    tok = crypto.encrypt(k1, "secret")
    with pytest.raises(Exception):
        crypto.decrypt(k2, tok)

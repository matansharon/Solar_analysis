from solaranalysis.web import auth


def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret")
    assert h.startswith("pbkdf2_sha256$")
    assert auth.verify_password("s3cret", h) is True
    assert auth.verify_password("wrong", h) is False


def test_cookie_valid_then_epoch_invalidates():
    key = b"0" * 32
    c = auth.make_cookie(key, epoch=1)
    assert auth.check_cookie(key, c, current_epoch=1) is True
    # Password change bumps epoch -> old cookie rejected.
    assert auth.check_cookie(key, c, current_epoch=2) is False


def test_cookie_tamper_rejected():
    key = b"0" * 32
    c = auth.make_cookie(key, epoch=1)
    tampered = c[:-2] + ("aa" if not c.endswith("aa") else "bb")
    assert auth.check_cookie(key, tampered, current_epoch=1) is False


def test_cookie_wrong_key_rejected():
    c = auth.make_cookie(b"0" * 32, epoch=1)
    assert auth.check_cookie(b"1" * 32, c, current_epoch=1) is False


def test_rate_limiter_blocks_after_max():
    t = {"now": 1000.0}
    rl = auth.RateLimiter(max_fails=3, window_s=60, now_fn=lambda: t["now"])
    ip = "10.0.0.5"
    for _ in range(3):
        rl.record_failure(ip)
    assert rl.is_blocked(ip) is True
    t["now"] += 61  # window elapsed
    assert rl.is_blocked(ip) is False


def test_rate_limiter_reset_clears():
    rl = auth.RateLimiter(max_fails=1, window_s=60, now_fn=lambda: 0.0)
    rl.record_failure("ip")
    assert rl.is_blocked("ip") is True
    rl.reset("ip")
    assert rl.is_blocked("ip") is False


def test_cookie_non_dict_payload_rejected():
    import base64, json, hmac as _h, hashlib
    key = b"0" * 32
    # A validly-signed cookie whose payload is a JSON list, not an object.
    payload = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        _h.new(key, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    assert auth.check_cookie(key, f"{payload}.{sig}", current_epoch=1) is False


def test_verify_password_none_stored_is_false():
    assert auth.verify_password("anything", None) is False

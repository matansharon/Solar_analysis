from solaranalysis.web import events


def test_redactor_masks_all_secrets():
    r = events.Redactor(["hunter2", "tok-abc"])
    out = r.redact("user pw=hunter2 token=tok-abc done")
    assert "hunter2" not in out and "tok-abc" not in out
    assert out.count("***") == 2


def test_redactor_ignores_empty_secrets():
    r = events.Redactor(["", None, "pw"])
    assert r.redact("x pw y") == "x *** y"
    assert r.redact("nothing here") == "nothing here"


def test_redactor_overlapping_longest_first():
    r = events.Redactor(["abc", "abcdef"])
    # The longer secret must be fully masked, not leave "def".
    assert r.redact("val=abcdef") == "val=***"


def test_event_roundtrip():
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        events.emit_event({"event": "plant_start", "plant": "A"})
    line = buf.getvalue().strip()
    kind, val = events.parse_line(line)
    assert kind == "event" and val["plant"] == "A"


def test_parse_plain_line():
    kind, val = events.parse_line("[warn] something happened")
    assert kind == "log" and val == "[warn] something happened"


def test_redactor_partial_overlap_no_fragment_leak():
    r = events.Redactor(["wxyz", "yzab"])
    out = r.redact("val=wxyzab end")
    # The union of both secrets' spans must be fully masked -- no "ab" fragment.
    assert "wxyz" not in out and "yzab" not in out
    assert "ab end" not in out
    assert out == "val=*** end"


def test_redactor_adjacent_secrets_merge():
    r = events.Redactor(["foo", "bar"])
    # Two distinct secrets touching -> a single merged mask.
    assert r.redact("x foobar y") == "x *** y"

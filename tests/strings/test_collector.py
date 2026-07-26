import json
from datetime import date
from pathlib import Path

import pytest

from solaranalysis.web import db
from solaranalysis.strings import collector, store
from solaranalysis.strings.mappers import InverterInfo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
INV = InverterInfo(serial="SN-A", model="MAX 70KTL3 LV", datalog_sn="DL-1",
                   nominal_power_w=70000.0, plant_id="10950561")


def _rows():
    payload = json.loads(
        (FIXTURES / "growatt_max_history.json").read_text(encoding="utf-8"))
    return payload["obj"]["datas"]


class FakeBS:
    """Serves the fixture rows as page 0, then an empty page — a complete
    short day, which is what the fixture's 3 rows represent."""
    def __init__(self, pages=None, fail_page=None):
        self.pages = pages if pages is not None else [_rows(), [], [], []]
        self.fail_page = fail_page
        self.requested = []

    def post_json(self, url, **kw):
        page = int(kw["form"]["start"])
        self.requested.append(page)
        if page == self.fail_page:
            raise RuntimeError("net::ERR_TIMED_OUT")
        datas = self.pages[page] if page < len(self.pages) else []
        return {"result": 1, "obj": {"datas": datas,
                                     "haveNext": page + 1 < len(self.pages)}}


def test_day_range_counts_back_from_target():
    assert collector.day_range(date(2026, 7, 25), 1) == ["2026-07-25"]
    assert collector.day_range(date(2026, 7, 25), 3) == [
        "2026-07-23", "2026-07-24", "2026-07-25"]


def test_fetch_day_rows_walks_all_four_pages():
    bs = FakeBS(pages=[_rows(), _rows(), _rows(), _rows()])
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert bs.requested == [0, 1, 2, 3]
    assert pages_ok == 4 and complete is True
    assert len(rows) == 12                      # 3 fixture rows x 4 pages


def test_fetch_day_rows_stops_when_have_next_is_false():
    bs = FakeBS(pages=[_rows()])                # haveNext False after page 0
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert bs.requested == [0]
    # a short day is still a COMPLETE day -- the portal said there was no more
    assert pages_ok == 1 and complete is True and len(rows) == 3


def test_fetch_day_rows_raises_when_page_zero_fails():
    bs = FakeBS(fail_page=0)
    with pytest.raises(RuntimeError):
        collector.fetch_day_rows(bs, "SN-A", "2026-07-25", "DL-1")


def test_fetch_day_rows_tolerates_a_later_page_failing():
    bs = FakeBS(pages=[_rows(), _rows(), _rows(), _rows()], fail_page=2)
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert pages_ok == 2 and complete is False   # pages 0 and 1 landed
    assert len(rows) == 6


def test_fetch_day_rows_flags_truncation_past_the_page_cap():
    # Five pages of data: the walk stops at the cap with haveNext still true,
    # which must be reported rather than passed off as a complete day.
    bs = FakeBS(pages=[_rows()] * 5)
    rows, pages_ok, complete = collector.fetch_day_rows(
        bs, "SN-A", "2026-07-25", "DL-1")
    assert pages_ok == collector.PAGES_PER_DAY
    assert complete is False


def test_collect_day_persists_inventory_energy_and_samples():
    conn = db.connect(":memory:"); db.init_db(conn)
    res = collector.collect_day(FakeBS(), conn, INV, "2026-07-25", now="NOW")
    assert res["day"] == "2026-07-25"
    assert res["channels"] == 18                # 6 MPPT + 12 strings
    assert res["energy_rows"] == 6
    assert res["samples"] == 54                 # 18 channels x 3 rows
    assert res["partial"] is False               # stopped on an empty page

    chans = store.load_channels(conn, "SN-A")
    assert len(chans) == 18
    assert chans[0]["plant_uid"] == "growatt-10950561"

    energy = {r["channel_no"]: r for r in
              store.load_day_energy(conn, "SN-A", "2026-07-25")}
    assert energy[1]["energy_kwh"] == 57.3
    assert energy[1]["peak_w"] == 7975.5

    assert len(store.load_channel_samples(conn, "SN-A", "2026-07-25")) == 54
    assert len(store.load_inverter_samples(conn, "SN-A", "2026-07-25")) == 3


def test_collect_day_marks_a_partial_day():
    conn = db.connect(":memory:"); db.init_db(conn)
    bs = FakeBS(pages=[_rows(), _rows(), _rows(), _rows()], fail_page=1)
    res = collector.collect_day(bs, conn, INV, "2026-07-25", now="NOW")
    assert res["partial"] is True and res["pages_ok"] == 1
    # the day's energy still landed -- page 0 is what carries it
    assert len(store.load_day_energy(conn, "SN-A", "2026-07-25")) == 6


def test_collect_day_treats_an_empty_day_as_normal():
    conn = db.connect(":memory:"); db.init_db(conn)
    res = collector.collect_day(FakeBS(pages=[[]]), conn, INV, "2026-07-05",
                                now="NOW")
    assert res["empty"] is True
    assert "error" not in res
    assert store.load_day_energy(conn, "SN-A", "2026-07-05") == []
    assert store.load_channels(conn, "SN-A") == []


def test_collect_isolates_a_failing_day_and_rolls_it_back():
    conn = db.connect(":memory:"); db.init_db(conn)
    results = collector.collect(
        conn=conn, inv=INV, days=["2026-07-24", "2026-07-25"], now="NOW",
        bs_for={"2026-07-24": FakeBS(fail_page=0), "2026-07-25": FakeBS()})
    by = {r["day"]: r for r in results}
    assert "error" in by["2026-07-24"]
    assert by["2026-07-25"]["energy_rows"] == 6
    # the failing day wrote nothing; the good day is committed
    assert store.load_day_energy(conn, "SN-A", "2026-07-24") == []
    assert len(store.load_day_energy(conn, "SN-A", "2026-07-25")) == 6
    assert len(store.load_channels(conn, "SN-A")) == 18


def test_collect_uses_one_session_for_every_day():
    conn = db.connect(":memory:"); db.init_db(conn)
    bs = FakeBS()
    results = collector.collect(conn=conn, inv=INV,
                                days=["2026-07-24", "2026-07-25"], now="NOW",
                                bs=bs)
    assert len(results) == 2
    assert all("error" not in r for r in results)

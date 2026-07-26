import pytest

from solaranalysis.strings import history_client as hc


class FakeBS:
    """Records form-encoded POSTs; returns a canned envelope."""
    def __init__(self, fail_times=0):
        self.posts = []
        self.fail_times = fail_times

    def post_json(self, url, **kw):
        self.posts.append((url, kw.get("form")))
        if len(self.posts) <= self.fail_times:
            raise RuntimeError("net::ERR_TIMED_OUT")
        return {"result": 1, "obj": {"datas": []}}


def test_get_plant_list_posts_to_the_index_endpoint():
    class ListBS(FakeBS):
        def post_json(self, url, **kw):
            self.posts.append((url, kw.get("form")))
            return [{"id": "10950561", "plantName": "Elcam Baram"}]

    bs = ListBS()
    out = hc.get_plant_list(bs)
    assert bs.posts[0][0] == "https://server.growatt.com/index/getPlantListTitle"
    assert out == [{"id": "10950561", "plantName": "Elcam Baram"}]


def test_get_plant_list_returns_empty_list_for_null_body():
    class NullBS:
        def post_json(self, url, **kw):
            return None
    assert hc.get_plant_list(NullBS()) == []


def test_get_device_list_posts_form_encoded():
    bs = FakeBS()
    hc.get_device_list(bs, "10950561")
    url, form = bs.posts[0]
    assert url == "https://server.growatt.com/panel/getDevicesByPlantList"
    assert form == {"plantId": "10950561", "currPage": "1"}


def test_get_history_page_sends_page_index_and_datalog():
    bs = FakeBS()
    hc.get_history_page(bs, "MZHRF6K002", "2026-07-25", 2, datalog_sn="XGD6CH21BK")
    url, form = bs.posts[0]
    assert url == "https://server.growatt.com/device/getMAXHistory"
    # start is a 0-based PAGE index, and start/end date bracket a single day
    assert form == {"maxSn": "MZHRF6K002", "startDate": "2026-07-25",
                    "endDate": "2026-07-25", "start": "2",
                    "allDatalogSns": "XGD6CH21BK"}


def test_get_history_page_retries_a_stalled_request():
    bs = FakeBS(fail_times=2)
    slept = []
    out = hc.get_history_page(bs, "SN", "2026-07-25", 0, sleep=slept.append)
    assert out == {"result": 1, "obj": {"datas": []}}
    assert len(bs.posts) == 3          # two failures then a success
    assert slept == [hc.RETRY_DELAY_S, hc.RETRY_DELAY_S]


def test_get_history_page_propagates_after_exhausting_retries():
    bs = FakeBS(fail_times=99)
    with pytest.raises(RuntimeError):
        hc.get_history_page(bs, "SN", "2026-07-25", 0, sleep=lambda s: None)
    assert len(bs.posts) == hc.RETRY_ATTEMPTS


def test_get_history_page_returns_empty_dict_for_null_body():
    class NullBS:
        def post_json(self, url, **kw):
            return None
    assert hc.get_history_page(NullBS(), "SN", "2026-07-25", 0) == {}

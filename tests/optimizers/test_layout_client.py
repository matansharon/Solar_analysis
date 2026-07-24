from solaranalysis.optimizers import layout_client as lc


class FakeBS:
    def __init__(self):
        self.gets, self.posts = [], []
    def get_json(self, url):
        self.gets.append(url)
        return {"ok": url}
    def post_json(self, url, **kw):
        self.posts.append(url)
        return {"page": []}


def test_logical_tree_url():
    bs = FakeBS()
    lc.get_logical_tree(bs, 2387929)
    assert bs.gets == [
        "https://monitoring.solaredge.com/services/layout/logical/generic/v2/site/2387929?include-optimizers=true"]


def test_by_inverter_energy_url_joins_serials_and_dates():
    bs = FakeBS()
    lc.get_by_inverter_energy(bs, 2387929, "2026-07-22", ["7E04A726-4F", "7E04A823-4D"])
    url = bs.gets[0]
    assert url.startswith(
        "https://monitoring.solaredge.com/services/layout/energy/site/2387929/by-inverter?")
    assert "start-date=2026-07-22" in url and "end-date=2026-07-22" in url
    assert "inverter-serials=7E04A726-4F,7E04A823-4D" in url
    assert "include-color=true" in url


def test_site_list_uses_post():
    bs = FakeBS()
    out = lc.get_site_list(bs)
    assert bs.posts == ["https://monitoring.solaredge.com/services/sitelist/searchSites"]
    assert out == {"page": []}

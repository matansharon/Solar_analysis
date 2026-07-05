import os
from solaranalysis.web.paths import Paths


def test_create_resolves_absolute_and_makes_dirs(tmp_path):
    data = tmp_path / "d"
    app = tmp_path / "a"
    app.mkdir()
    p = Paths.create(str(data), str(app))
    assert os.path.isabs(p.data_dir) and os.path.isabs(p.app_dir)
    assert os.path.isdir(p.logs_dir)
    assert os.path.isdir(p.output_dir)
    assert os.path.isdir(p.session_cache_dir)
    assert p.db_path == os.path.join(p.data_dir, "app.db")
    assert p.key_path == os.path.join(p.data_dir, "secret.key")
    assert p.config_yaml == os.path.join(p.app_dir, "config.yaml")
    assert p.env_file == os.path.join(p.app_dir, ".env")


def test_paths_independent_of_cwd(tmp_path, monkeypatch):
    data = tmp_path / "d"
    app = tmp_path / "a"
    app.mkdir()
    p = Paths.create(str(data), str(app))
    monkeypatch.chdir(tmp_path)
    # Re-reading the property must not depend on cwd.
    assert os.path.isabs(p.output_dir)

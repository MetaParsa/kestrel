"""The judge demo: committed sample data is intact and the comparison logic is honest."""
from judge import demo


def test_sample_data_matches_its_manifest():
    assert demo.check_data() == []


def test_rows_flag_differences():
    assert demo.row("x", 0.014, 0.014)["same"]
    assert not demo.row("x", 0.014, 0.015)["same"]
    assert not demo.row("x", None, 0.0)["same"]
    assert not demo.row("x", None, None)["same"]                 # a step that produced nothing never passes


def test_headline_numbers_only_touch_marked_text(tmp_path, monkeypatch):
    import json
    from reports import headline_numbers
    (tmp_path / "reports" / "out").mkdir(parents=True)
    (tmp_path / "reports" / "out" / "self-grading.json").write_text(json.dumps({"summary": {
        "forecasts_resolved": 10, "claude_playbooks": 6, "playbooks_citing_own_scorecard": 4,
        "direction_hit": 0.5, "naive_hit": 0.3, "always_flat_hit": 0.4}}), encoding="utf-8")
    (tmp_path / "README.md").write_text("before <!-- SG:en -->old<!-- /SG --> after", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    headline_numbers.main()
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert text.startswith("before <!-- SG:en -->10 forecasts: Claude 50 %") and text.endswith("<!-- /SG --> after")
    headline_numbers.main()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == text


def test_judge_demo_keeps_the_film_in_its_default_folder(tmp_path, monkeypatch):
    from judge import demo
    film = demo.REPO / "judge_out" / "film"
    film.mkdir(parents=True, exist_ok=True)
    keep = film / "keep.txt"
    keep.write_text("x", encoding="utf-8")
    monkeypatch.setattr(demo, "_run_all", lambda *a, **k: None)     # no child processes: only the folder logic
    monkeypatch.setattr(demo, "compare", lambda *a, **k: [])
    demo.main(["--quick"])
    assert keep.exists() and (demo.REPO / "judge_out" / ".judge_demo").exists()
    keep.unlink()

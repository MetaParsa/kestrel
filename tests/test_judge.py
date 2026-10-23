"""The judge demo: committed sample data is intact and the comparison logic is honest."""
from judge import demo


def test_sample_data_matches_its_manifest():
    assert demo.check_data() == []


def test_rows_flag_differences():
    assert demo.row("x", 0.014, 0.014)["same"]
    assert not demo.row("x", 0.014, 0.015)["same"]
    assert not demo.row("x", None, 0.0)["same"]

"""
Tests for prune_course_data: keep newest file per active term, drop the rest.
"""
import os

import src.scraper.scraper as scraper


def _make(path, name, mtime):
    f = path / name
    f.write_text("{}", encoding="utf-8")
    os.utime(f, (mtime, mtime))
    return f


class TestPruneCourseData:
    def test_keeps_newest_active_drops_old_and_inactive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scraper, "COURSE_DATA_DIR", tmp_path)

        spring = _make(tmp_path, "spring2026_2025-12-19_00-00-00.json", 1000)   # ended term
        summer_old = _make(tmp_path, "summer2026_2025-12-19_00-00-00.json", 1000)  # stale dup
        summer_new = _make(tmp_path, "summer2026_2026-06-03_00-00-00.json", 2000)  # newest active
        fall = _make(tmp_path, "fall2026_2026-06-03_00-00-00.json", 2000)

        active = [
            {"code": "1", "description": "Fall 2026"},
            {"code": "2", "description": "Summer 2026"},
        ]
        scraper.prune_course_data(active)

        remaining = {p.name for p in tmp_path.glob("*.json")}
        assert remaining == {summer_new.name, fall.name}
        assert not spring.exists()       # inactive term removed
        assert not summer_old.exists()   # older duplicate removed

    def test_no_active_terms_is_safe_noop_on_empty_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scraper, "COURSE_DATA_DIR", tmp_path)
        scraper.prune_course_data([])  # nothing to do, must not raise
        assert list(tmp_path.glob("*.json")) == []

"""
Tests for course data loading utilities in src/utils/course_loader.py
"""
import pytest
import json
from pathlib import Path
from src.utils.course_loader import load_all_semesters


class TestLoadAllSemesters:
    """Test the load_all_semesters function"""

    def test_loads_json_files(self, temp_course_data_dir):
        """Should load courses from JSON files"""
        courses = load_all_semesters(directory=str(temp_course_data_dir))
        assert len(courses) == 2
        assert "12345" in courses
        assert "67890" in courses

    def test_extracts_term_from_filename(self, temp_course_data_dir):
        """Should extract term from filename (e.g., fall2025_*.json -> fall2025)"""
        courses = load_all_semesters(directory=str(temp_course_data_dir))
        course = courses["12345"]["course"]
        assert course.get("source_term") == "fall2025"

    def test_adds_source_term_to_courses(self, temp_course_data_dir):
        """Each course should have source_term added"""
        courses = load_all_semesters(directory=str(temp_course_data_dir))
        for crn, data in courses.items():
            if "course" in data:
                assert "source_term" in data["course"]

    def test_handles_empty_directory(self, tmp_path):
        """Should return empty dict for empty directory"""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        courses = load_all_semesters(directory=str(empty_dir))
        assert courses == {}

    def test_handles_multiple_semester_files(self, tmp_path):
        """Should combine courses from multiple semester files"""
        data_dir = tmp_path / "course_data"
        data_dir.mkdir()

        # Create fall semester file
        fall_data = {
            "11111": {
                "course": {
                    "CRN": "11111",
                    "subject": "CSCI",
                    "courseTitle": "Fall Course"
                }
            }
        }
        (data_dir / "fall2025_2025-01-01.json").write_text(json.dumps(fall_data))

        # Create spring semester file
        spring_data = {
            "22222": {
                "course": {
                    "CRN": "22222",
                    "subject": "MATH",
                    "courseTitle": "Spring Course"
                }
            }
        }
        (data_dir / "spring2026_2025-01-01.json").write_text(json.dumps(spring_data))

        courses = load_all_semesters(directory=str(data_dir))

        assert len(courses) == 2
        assert "11111" in courses
        assert "22222" in courses
        assert courses["11111"]["course"]["source_term"] == "fall2025"
        assert courses["22222"]["course"]["source_term"] == "spring2026"

    def test_course_data_structure(self, temp_course_data_dir):
        """Should preserve course data structure"""
        courses = load_all_semesters(directory=str(temp_course_data_dir))
        course = courses["12345"]["course"]

        assert course["CRN"] == "12345"
        assert course["subject"] == "CSCI"
        assert course["courseNumber"] == "0010"
        assert course["courseTitle"] == "Intro to CS"
        assert "faculty" in course
        assert "meetings" in course

    def test_handles_filename_without_underscore(self, tmp_path):
        """Should handle filenames without underscore (uses stem as term)"""
        data_dir = tmp_path / "course_data"
        data_dir.mkdir()

        data = {
            "33333": {
                "course": {
                    "CRN": "33333",
                    "courseTitle": "Test"
                }
            }
        }
        (data_dir / "summer2025.json").write_text(json.dumps(data))

        courses = load_all_semesters(directory=str(data_dir))
        assert courses["33333"]["course"]["source_term"] == "summer2025"

    def test_newest_file_per_term_wins(self, tmp_path):
        """When a term has multiple files, only the newest should be loaded."""
        import os
        data_dir = tmp_path / "course_data"
        data_dir.mkdir()

        old = data_dir / "fall2026_2026-01-01_00-00-00.json"
        new = data_dir / "fall2026_2026-06-01_00-00-00.json"
        old.write_text(json.dumps({"111": {"course": {"CRN": "111", "courseTitle": "OLD"}}}))
        new.write_text(json.dumps({"111": {"course": {"CRN": "111", "courseTitle": "NEW"}}}))
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))

        courses = load_all_semesters(directory=str(data_dir))
        # Only the newer file's version of CRN 111 should survive.
        assert courses["111"]["course"]["courseTitle"] == "NEW"

"""
Shared utilities for loading course data from JSON files.

This module provides a centralized function to load all semester course data
from JSON files in the course_data directory.
"""

import json
from pathlib import Path


def load_all_semesters(directory="course_data"):
    """
    Load all course data from JSON files in the specified directory.

    Scans the directory for JSON files (e.g., fall2025_*.json, spring2026_*.json),
    reads course data from each file, and combines them into a single dictionary.
    Each course is tagged with its source semester.

    Args:
        directory: Path to directory containing semester JSON files (default: "course_data")

    Returns:
        dict: Dictionary mapping CRN to course data, with source_term added to each course
              Format: {CRN: {course: {..., source_term: "fall2025"}, ...}}

    Example:
        >>> courses = load_all_semesters()
        >>> print(courses["12345"]["course"]["source_term"])
        "fall2025"
    """
    all_courses = {}

    for json_file in Path(directory).glob("*.json"):
        with open(json_file, 'r') as f:
            data = json.load(f)

            # Extract term from filename (e.g., "fall2025" from "fall2025_2025-11-26.json")
            term = json_file.stem.split('_')[0] if '_' in json_file.stem else json_file.stem

            for crn, course_data in data.items():
                if 'course' in course_data:
                    course_data['course']['source_term'] = term
                all_courses[crn] = course_data

    return all_courses

"""The hourly refresh should preserve vectors for courses whose text is unchanged."""
import copy
import json

import faiss
import numpy as np

from src.embeddings import fast


def _course(title, enrolled=0):
    return {"course": {"title": title, "enrollment": {"enrolled": enrolled}}}


def _seed(tmp_path, monkeypatch, old_courses, vectors, current_courses):
    index_path = tmp_path / "courses.index"
    metadata_path = tmp_path / "id_to_course.json"
    hashes_path = tmp_path / "course_hashes.json"
    no_enrollment_path = tmp_path / "course_hashes_no_enrollment.json"

    index = faiss.IndexFlatL2(3)
    index.add(np.asarray(vectors, dtype="float32"))
    faiss.write_index(index, str(index_path))
    metadata_path.write_text(json.dumps([
        {"crn": crn, "course": course} for crn, course in old_courses.items()
    ]))
    hashes_path.write_text(json.dumps({
        crn: fast.compute_course_hash(course) for crn, course in old_courses.items()
    }))
    no_enrollment_path.write_text(json.dumps({
        crn: fast.compute_course_hash_without_enrollment(course)
        for crn, course in old_courses.items()
    }))

    monkeypatch.setattr(fast, "index_file", str(index_path))
    monkeypatch.setattr(fast, "metadata_file", str(metadata_path))
    monkeypatch.setattr(fast, "dimension", 3)
    monkeypatch.setattr(fast, "COURSE_HASHES_NO_ENROLLMENT_JSON", no_enrollment_path)
    monkeypatch.setattr(fast, "load_all_semesters", lambda: copy.deepcopy(current_courses))
    monkeypatch.setattr(fast, "load_course_hashes", lambda: json.loads(hashes_path.read_text()))
    monkeypatch.setattr(fast, "save_course_hashes", lambda hashes: hashes_path.write_text(json.dumps(hashes)))
    monkeypatch.setattr(fast, "course_to_text", lambda course: course["course"]["title"])
    return index_path, metadata_path


def test_no_changes_does_not_read_or_rewrite_index(tmp_path, monkeypatch):
    courses = {"A": _course("Algebra"), "B": _course("Biology")}
    index_path, metadata_path = _seed(
        tmp_path, monkeypatch, courses, [[1, 0, 0], [0, 1, 0]], courses
    )
    original_index = index_path.read_bytes()
    original_metadata = metadata_path.read_bytes()

    def unexpected(*args, **kwargs):
        raise AssertionError("unchanged courses must not rebuild or re-embed")

    monkeypatch.setattr(fast.faiss, "read_index", unexpected)
    monkeypatch.setattr(fast, "get_embeddings_batch", unexpected)
    fast.fast_incremental_update()

    assert index_path.read_bytes() == original_index
    assert metadata_path.read_bytes() == original_metadata


def test_mixed_changes_reuse_unchanged_vectors(tmp_path, monkeypatch):
    old = {
        "A": _course("Algebra"),
        "B": _course("Biology", enrolled=1),
        "C": _course("Chemistry"),
        "E": _course("Economics"),
    }
    current = {
        "A": _course("Algebra"),
        "B": _course("Biology", enrolled=2),
        "C": _course("Advanced Chemistry"),
        "D": _course("Design"),
    }
    index_path, metadata_path = _seed(
        tmp_path, monkeypatch, old,
        [[1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 0]], current,
    )
    embedded = []

    def embed(texts):
        embedded.extend(texts)
        return [{"Advanced Chemistry": [2, 0, 0], "Design": [0, 2, 0]}[text]
                for text in texts]

    monkeypatch.setattr(fast, "get_embeddings_batch", embed)
    fast.fast_incremental_update()

    entries = json.loads(metadata_path.read_text())
    index = faiss.read_index(str(index_path))
    vectors = {entry["crn"]: index.reconstruct(i).tolist()
               for i, entry in enumerate(entries)}
    assert set(embedded) == {"Advanced Chemistry", "Design"}
    assert vectors == {
        "A": [1, 0, 0],
        "B": [0, 1, 0],
        "C": [2, 0, 0],
        "D": [0, 2, 0],
    }
    assert entries[1]["course"]["course"]["enrollment"]["enrolled"] == 2

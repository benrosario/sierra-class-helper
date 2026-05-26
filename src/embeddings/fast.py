"""
Super-fast incremental update that skips re-embedding for enrollment-only changes
Use this for hourly updates where only enrollment numbers change
"""
import json
import faiss
import os
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from src.utils.course_loader import load_all_semesters
from src.utils.paths import COURSE_HASHES_NO_ENROLLMENT_JSON
from src.embeddings.incremental import (
    compute_course_hash,
    load_course_hashes,
    save_course_hashes,
    course_to_text,
    get_embeddings_batch,
    dimension,
    index_file,
    metadata_file,
    hash_file
)
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def compute_course_hash_without_enrollment(course_data):
    """Compute hash excluding enrollment data to detect structural changes"""
    course_copy = json.loads(json.dumps(course_data))  # Deep copy

    # Remove enrollment data (this changes frequently but doesn't affect embeddings much)
    if 'course' in course_copy and 'enrollment' in course_copy['course']:
        course_copy['course']['enrollment'] = {}

    course_str = json.dumps(course_copy, sort_keys=True)
    return hashlib.md5(course_str.encode()).hexdigest()

def fast_incremental_update():
    """
    Ultra-fast update that only re-embeds courses with structural changes
    Courses with only enrollment changes get metadata updated without re-embedding
    """
    start_time = datetime.now()

    # Load current courses
    logger.info("Loading current course data...")
    current_courses = load_all_semesters()

    # Load previous hashes
    logger.info("Loading previous course hashes...")
    old_hashes = load_course_hashes()

    # Compute new hashes (full hash for tracking)
    logger.info("Computing hashes for current courses...")
    new_hashes = {}
    new_hashes_no_enrollment = {}

    for crn, course_data in current_courses.items():
        new_hashes[crn] = compute_course_hash(course_data)
        new_hashes_no_enrollment[crn] = compute_course_hash_without_enrollment(course_data)

    # Also load old hashes without enrollment (if exists)
    hash_no_enroll_file = str(COURSE_HASHES_NO_ENROLLMENT_JSON)
    if os.path.exists(hash_no_enroll_file):
        with open(hash_no_enroll_file, 'r') as f:
            old_hashes_no_enrollment = json.load(f)
    else:
        old_hashes_no_enrollment = {}

    # Identify changes
    new_crns = set(new_hashes.keys()) - set(old_hashes.keys())
    removed_crns = set(old_hashes.keys()) - set(new_hashes.keys())

    # Structural changes (need re-embedding)
    structural_changes = {
        crn for crn in new_hashes.keys() & old_hashes.keys()
        if new_hashes_no_enrollment.get(crn) != old_hashes_no_enrollment.get(crn)
    }

    # Enrollment-only changes (just update metadata)
    enrollment_only = {
        crn for crn in new_hashes.keys() & old_hashes.keys()
        if new_hashes[crn] != old_hashes[crn]
        and new_hashes_no_enrollment.get(crn) == old_hashes_no_enrollment.get(crn)
    }

    logger.info(
        f"Change summary: {len(new_crns)} new, {len(structural_changes)} structural changes, "
        f"{len(enrollment_only)} enrollment-only, {len(removed_crns)} removed"
    )
    logger.info(f"Re-embedding needed for: {len(new_crns) + len(structural_changes)} courses")
    logger.info(f"Metadata-only update for: {len(enrollment_only)} courses")

    # Load existing index
    if os.path.exists(index_file) and os.path.exists(metadata_file):
        logger.info("Loading existing index...")
        index = faiss.read_index(index_file)
        with open(metadata_file, 'r', encoding='utf-8') as f:
            id_to_course_list = json.load(f)

        # Quick path: Only enrollment changes, no structural changes
        if not new_crns and not removed_crns and not structural_changes and enrollment_only:
            logger.info("FAST PATH: Only enrollment changed, updating metadata in-place...")

            # Update metadata without re-embedding
            for i, entry in enumerate(id_to_course_list):
                crn = entry['crn']
                if crn in enrollment_only:
                    # Update the course data
                    id_to_course_list[i]['course'] = current_courses[crn]

            # Save updated metadata
            logger.info(f"Saving updated metadata for {len(enrollment_only)} courses...")
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(id_to_course_list, f, indent=2)

            # Save hashes
            save_course_hashes(new_hashes)
            with open(hash_no_enroll_file, 'w') as f:
                json.dump(new_hashes_no_enrollment, f, indent=2)

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"FAST UPDATE complete in {elapsed:.1f} seconds!")
            logger.info(f"Total OpenAI API calls: 0")
            logger.info(f"Estimated cost: $0.0000")
            return

        # Slow path: Need to rebuild index
        logger.info("Rebuilding index with changes...")
        new_index = faiss.IndexFlatL2(dimension)
        new_id_to_course_list = []

        # Collect courses that don't need re-embedding
        texts_to_embed = []
        entries_to_keep = []

        for entry in id_to_course_list:
            crn = entry['crn']

            if crn in removed_crns:
                continue  # Skip removed courses

            if crn in structural_changes:
                continue  # Will re-embed below

            if crn in enrollment_only:
                # Update metadata but keep embedding
                texts_to_embed.append(course_to_text(entry['course']))
                entries_to_keep.append({"crn": crn, "course": current_courses[crn]})
            elif crn not in new_crns:
                # Unchanged course
                texts_to_embed.append(course_to_text(entry['course']))
                entries_to_keep.append(entry)

        # Get embeddings for unchanged courses
        if texts_to_embed:
            logger.info(f"Re-embedding {len(texts_to_embed)} unchanged courses (required for index rebuild)...")
            embeddings = get_embeddings_batch(texts_to_embed)
            for embedding, entry in zip(embeddings, entries_to_keep):
                vector = np.array([embedding], dtype="float32")
                new_index.add(vector)
                new_id_to_course_list.append(entry)

        # Process structural changes
        if structural_changes:
            logger.info(f"Embedding {len(structural_changes)} structurally changed courses...")
            changed_texts = []
            changed_entries = []

            for crn in structural_changes:
                course = current_courses[crn]
                changed_texts.append(course_to_text(course))
                changed_entries.append({"crn": crn, "course": course})

            if changed_texts:
                embeddings = get_embeddings_batch(changed_texts)
                for embedding, entry in zip(embeddings, changed_entries):
                    vector = np.array([embedding], dtype="float32")
                    new_index.add(vector)
                    new_id_to_course_list.append(entry)

        # Process new courses
        if new_crns:
            logger.info(f"Embedding {len(new_crns)} new courses...")
            new_texts = []
            new_entries = []

            for crn in new_crns:
                course = current_courses[crn]
                new_texts.append(course_to_text(course))
                new_entries.append({"crn": crn, "course": course})

            if new_texts:
                embeddings = get_embeddings_batch(new_texts)
                for embedding, entry in zip(embeddings, new_entries):
                    vector = np.array([embedding], dtype="float32")
                    new_index.add(vector)
                    new_id_to_course_list.append(entry)

        index = new_index
        id_to_course_list = new_id_to_course_list

    else:
        # No existing index - create from scratch
        logger.info("No existing index found. Run embeddings_incremental.py first for initial setup.")
        return

    # Save everything
    logger.info(f"Saving index with {len(id_to_course_list)} courses...")
    faiss.write_index(index, index_file)
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(id_to_course_list, f, indent=2)
    save_course_hashes(new_hashes)
    with open(hash_no_enroll_file, 'w') as f:
        json.dump(new_hashes_no_enrollment, f, indent=2)

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Update complete in {elapsed:.1f} seconds!")

    total_api_calls = len(texts_to_embed) + len(structural_changes) + len(new_crns)
    logger.info(f"Total OpenAI API calls: {total_api_calls}")
    logger.info(f"Estimated cost: ${total_api_calls * 0.00002:.4f}")

if __name__ == "__main__":
    fast_incremental_update()

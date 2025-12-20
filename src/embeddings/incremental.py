"""
Incremental embedding update system
Only creates embeddings for new or changed courses
"""
import json
import faiss
import os
import logging
import numpy as np
import hashlib
from pathlib import Path
from openai import OpenAI
from datetime import datetime

# Import shared utilities
from src.utils.course_formatting import informalName, meetingDays
from src.utils.campus import get_campus
from src.utils.course_loader import load_all_semesters
from src.utils.embedding_helpers import (
    get_embeddings_batch as get_embeddings_batch_helper,
    get_embedding as get_embedding_helper,
    course_to_text as course_to_text_helper
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Grab API key with validation
api_key = os.environ.get("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY environment variable is required")
client = OpenAI(api_key=api_key)

dimension = 1536
index_file = "courses.index"
metadata_file = "id_to_course.json"
hash_file = "course_hashes.json"  # Track which courses have changed

def compute_course_hash(course_data):
    """Compute a hash of course data to detect changes"""
    # Convert to stable JSON string (sorted keys)
    course_str = json.dumps(course_data, sort_keys=True)
    return hashlib.md5(course_str.encode()).hexdigest()

def load_course_hashes():
    """Load previously computed hashes"""
    if os.path.exists(hash_file):
        with open(hash_file, 'r') as f:
            return json.load(f)
    return {}

def save_course_hashes(hashes):
    """Save course hashes"""
    with open(hash_file, 'w') as f:
        json.dump(hashes, f, indent=2)

# Wrapper functions to use shared utilities
def course_to_text(course):
    """Convert course to text using shared utility function."""
    return course_to_text_helper(course, informalName, meetingDays, get_campus)

def get_embedding(text: str) -> list[float]:
    """Get embedding for a single text using shared utility."""
    return get_embedding_helper(client, text)

def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Get embeddings in batches using shared utility."""
    return get_embeddings_batch_helper(client, texts)

def incremental_update():
    """Update embeddings incrementally"""
    start_time = datetime.now()

    # Load current courses
    logger.info("Loading current course data...")
    current_courses = load_all_semesters()

    # Load previous hashes
    logger.info("Loading previous course hashes...")
    old_hashes = load_course_hashes()

    # Compute new hashes
    logger.info("Computing hashes for current courses...")
    new_hashes = {}
    for crn, course_data in current_courses.items():
        new_hashes[crn] = compute_course_hash(course_data)

    # Identify changes
    new_crns = set(new_hashes.keys()) - set(old_hashes.keys())
    removed_crns = set(old_hashes.keys()) - set(new_hashes.keys())
    changed_crns = {
        crn for crn in new_hashes.keys() & old_hashes.keys()
        if new_hashes[crn] != old_hashes[crn]
    }

    logger.info(f"Change summary: {len(new_crns)} new, {len(changed_crns)} changed, {len(removed_crns)} removed")

    # Load existing index if it exists
    if os.path.exists(index_file) and os.path.exists(metadata_file):
        logger.info("Loading existing index...")
        index = faiss.read_index(index_file)
        with open(metadata_file, 'r', encoding='utf-8') as f:
            id_to_course_list = json.load(f)

        # Remove old courses
        if removed_crns:
            logger.info(f"Removing {len(removed_crns)} obsolete courses...")
            # Create new index without removed courses
            new_index = faiss.IndexFlatL2(dimension)
            new_id_to_course_list = []

            # Batch process remaining courses
            texts_to_embed = []
            entries_to_keep = []

            for entry in id_to_course_list:
                if entry['crn'] not in removed_crns:
                    texts_to_embed.append(course_to_text(entry['course']))
                    entries_to_keep.append(entry)

            # Get all embeddings in batches
            if texts_to_embed:
                embeddings = get_embeddings_batch(texts_to_embed)
                for embedding, entry in zip(embeddings, entries_to_keep):
                    vector = np.array([embedding], dtype="float32")
                    new_index.add(vector)
                    new_id_to_course_list.append(entry)

            index = new_index
            id_to_course_list = new_id_to_course_list

        # Update changed courses (remove old, add new)
        if changed_crns:
            logger.info(f"Updating {len(changed_crns)} changed courses...")
            # Remove old versions
            new_index = faiss.IndexFlatL2(dimension)
            new_id_to_course_list = []

            # Batch process unchanged courses
            texts_to_embed = []
            entries_to_keep = []

            for entry in id_to_course_list:
                if entry['crn'] not in changed_crns:
                    texts_to_embed.append(course_to_text(entry['course']))
                    entries_to_keep.append(entry)

            # Get embeddings for unchanged courses
            if texts_to_embed:
                embeddings = get_embeddings_batch(texts_to_embed)
                for embedding, entry in zip(embeddings, entries_to_keep):
                    vector = np.array([embedding], dtype="float32")
                    new_index.add(vector)
                    new_id_to_course_list.append(entry)

            # Batch process updated courses
            updated_texts = []
            updated_entries = []
            for crn in changed_crns:
                course = current_courses[crn]
                updated_texts.append(course_to_text(course))
                updated_entries.append({"crn": crn, "course": course})

            # Get embeddings for updated courses
            if updated_texts:
                embeddings = get_embeddings_batch(updated_texts)
                for embedding, entry in zip(embeddings, updated_entries):
                    vector = np.array([embedding], dtype="float32")
                    new_index.add(vector)
                    new_id_to_course_list.append(entry)

            index = new_index
            id_to_course_list = new_id_to_course_list

        # Add new courses
        if new_crns:
            logger.info(f"Adding {len(new_crns)} new courses...")
            new_texts = []
            new_entries = []

            for crn in new_crns:
                course = current_courses[crn]
                new_texts.append(course_to_text(course))
                new_entries.append({"crn": crn, "course": course})

            # Batch process new courses
            if new_texts:
                embeddings = get_embeddings_batch(new_texts)
                for embedding, entry in zip(embeddings, new_entries):
                    vector = np.array([embedding], dtype="float32")
                    index.add(vector)
                    id_to_course_list.append(entry)

    else:
        # No existing index - create from scratch
        logger.info("No existing index found. Creating full index...")
        index = faiss.IndexFlatL2(dimension)
        id_to_course_list = []

        # Batch process all courses
        all_texts = []
        all_entries = []

        for crn, course in current_courses.items():
            try:
                all_texts.append(course_to_text(course))
                all_entries.append({"crn": crn, "course": course})
            except Exception as e:
                logger.error(f"Failed to process course {crn}: {e}")
                continue

        # Get all embeddings in batches
        if all_texts:
            embeddings = get_embeddings_batch(all_texts)
            for embedding, entry in zip(embeddings, all_entries):
                vector = np.array([embedding], dtype="float32")
                index.add(vector)
                id_to_course_list.append(entry)

    # Save everything
    logger.info(f"Saving index with {len(id_to_course_list)} courses...")
    faiss.write_index(index, index_file)
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(id_to_course_list, f, indent=2)
    save_course_hashes(new_hashes)

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Update complete in {elapsed:.1f} seconds!")

    # Only compute API calls for changes
    total_api_calls = len(new_crns) + len(changed_crns)
    logger.info(f"Total OpenAI API calls: {total_api_calls}")
    logger.info(f"Estimated cost: ${total_api_calls * 0.00002:.4f}")

if __name__ == "__main__":
    incremental_update()

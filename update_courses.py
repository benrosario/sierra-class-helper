#!/usr/bin/env python3
"""
Automated course update script for Sierra Class Helper
Scrapes course data and rebuilds embeddings

This script can be run:
- Manually: python update_courses.py
- Via Railway Cron: Scheduled to run hourly during registration periods
"""
import os
import sys
import logging
from datetime import datetime
import subprocess

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_command(command: list, description: str) -> bool:
    """
    Run a shell command and log the result

    Args:
        command: List of command parts (e.g., ['python', 'script.py'])
        description: Human-readable description for logging

    Returns:
        bool: True if successful, False if failed
    """
    logger.info(f"Starting: {description}")
    logger.info(f"Command: {' '.join(command)}")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )

        if result.returncode == 0:
            logger.info(f"✓ {description} completed successfully")
            if result.stdout:
                logger.info(f"Output: {result.stdout[:500]}")  # First 500 chars
            return True
        else:
            logger.error(f"✗ {description} failed with return code {result.returncode}")
            if result.stderr:
                logger.error(f"Error: {result.stderr[:500]}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"✗ {description} timed out after 10 minutes")
        return False
    except Exception as e:
        logger.error(f"✗ {description} failed with exception: {e}")
        return False

def main():
    """
    Main update workflow:
    1. Scrape latest course data from Sierra College
    2. Rebuild embeddings using fast incremental method
    """
    start_time = datetime.now()
    logger.info("="*60)
    logger.info("Starting automated course update")
    logger.info(f"Timestamp: {start_time.isoformat()}")
    logger.info("="*60)

    # Track overall success
    all_successful = True

    # Step 1: Run the scraper
    # The scraper_cli.py should handle scraping all active semesters
    scraper_success = run_command(
        ['python', 'scraper_cli.py'],
        "Course data scraping"
    )

    if not scraper_success:
        logger.error("Scraping failed - aborting update process")
        all_successful = False
    else:
        # Step 2: Rebuild embeddings
        # Use fast embeddings for incremental updates
        embeddings_success = run_command(
            ['python', 'embeddings_fast.py'],
            "Embeddings rebuild (fast mode)"
        )

        if not embeddings_success:
            logger.error("Embeddings rebuild failed")
            all_successful = False

    # Summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    logger.info("="*60)
    logger.info("Course update process completed")
    logger.info(f"Duration: {duration:.2f} seconds")
    logger.info(f"Status: {'SUCCESS ✓' if all_successful else 'FAILED ✗'}")
    logger.info("="*60)

    # Exit with appropriate code
    sys.exit(0 if all_successful else 1)

if __name__ == "__main__":
    main()

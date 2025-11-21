#!/bin/bash
# Script to update course data and rebuild embeddings

echo "========================================="
echo "Sierra Class Helper - Course Data Update"
echo "========================================="
echo ""

# Check if term is provided
if [ -z "$1" ]; then
    echo "Usage: ./update_courses.sh <term>"
    echo ""
    echo "Available terms:"
    echo "  - spring2025"
    echo "  - summer2025"
    echo "  - fall2025"
    echo "  - spring2026"
    echo ""
    echo "Example: ./update_courses.sh spring2026"
    exit 1
fi

TERM=$1

echo "Step 1: Scraping $TERM course data..."
python scraper_cli.py "$TERM"

if [ $? -ne 0 ]; then
    echo "ERROR: Scraping failed!"
    exit 1
fi

echo ""
echo "Step 2: Updating embeddings (fast mode - skips re-embedding for enrollment-only changes)..."
python embeddings_fast.py

if [ $? -ne 0 ]; then
    echo "ERROR: Embedding creation failed!"
    exit 1
fi

echo ""
echo "========================================="
echo "Update Complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "1. Restart your API server (Ctrl+C then run: python api_server.py)"
echo "2. Your Discord bot will automatically use the new data"
echo ""

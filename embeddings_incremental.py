#!/usr/bin/env python3
"""
Backward compatibility wrapper for incremental embeddings.
Runs from src.embeddings.incremental
"""
if __name__ == "__main__":
    from src.embeddings.incremental import incremental_update
    incremental_update()

#!/usr/bin/env python3
"""
Backward compatibility wrapper for fast embeddings.
Runs from src.embeddings.fast
"""
if __name__ == "__main__":
    from src.embeddings.fast import fast_incremental_update
    fast_incremental_update()

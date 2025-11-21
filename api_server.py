#!/usr/bin/env python3
"""
Backward compatibility wrapper for API server.
Runs the server from src.api.server
"""
if __name__ == "__main__":
    from src.api.server import app
    import uvicorn
    import os

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

#!/usr/bin/env python3
"""Entry point kept for deployment configs (Procfile, railway.toml, start_local.sh)."""
if __name__ == "__main__":
    import uvicorn
    from src.api.server import app
    from src.config import Config
    uvicorn.run(app, host="0.0.0.0", port=Config.PORT)

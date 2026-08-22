#!/bin/bash
# Helper script to start both API server and Discord bot locally

echo "Starting Sierra Class Helper..."
echo ""

# Check if .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found!"
    echo "Please copy .env.example to .env and fill in your API keys"
    exit 1
fi

# Check if index exists
if [ ! -f courses.index ]; then
    echo "ERROR: courses.index not found!"
    echo "Please run 'python -m src.embeddings.incremental' first to build the index"
    exit 1
fi

# Confirm a backgrounded process is still alive after a brief wait, so an exec
# failure (missing python, import error, uvicorn boot crash) surfaces here
# instead of behind a misleading "now running!" banner. Kills any siblings on
# failure so we never leave a half-started stack behind.
check_alive() {
    local name=$1 pid=$2
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "ERROR: $name (pid $pid) died on startup. Check the output above."
        # Kill anything else we already started.
        for other in "$@"; do
            [ "$other" != "$name" ] && [ "$other" != "$pid" ] && kill "$other" 2>/dev/null
        done
        exit 1
    fi
}

# Start API server in background
echo "Starting API server on port 8000..."
python3 api_server.py &
API_PID=$!
sleep 3
check_alive "API server" "$API_PID"

# Start Discord bot
echo "Starting Discord bot..."
echo ""
python3 discord_bot.py &
BOT_PID=$!
sleep 2
check_alive "Discord bot" "$BOT_PID" "$API_PID"

echo ""
echo "========================================="
echo "Sierra Class Helper is now running!"
echo "========================================="
echo "API Server PID: $API_PID"
echo "Discord Bot PID: $BOT_PID"
echo ""
echo "API available at: http://localhost:8000"
echo "API docs at: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both services"
echo ""

# Wait for interrupt
trap "kill $API_PID $BOT_PID; exit" INT
wait

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

# Start API server in background
echo "Starting API server on port 8000..."
python api_server.py &
API_PID=$!

# Wait for API to start
sleep 3

# Start Discord bot
echo "Starting Discord bot..."
echo ""
python discord_bot.py &
BOT_PID=$!

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

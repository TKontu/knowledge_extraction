#!/bin/bash
# Quick Docker test - minimal version
# Usage: ./docker_quick_test.sh

set -e

echo "Building image..."
docker build -t scristill-pipeline . -q

echo "Starting container..."
CONTAINER_ID=$(docker run -d -p 8000:8000 -e API_KEY=test scristill-pipeline)

echo "Waiting for startup..."
sleep 3

echo "Testing health endpoint..."
curl -sf http://localhost:8000/health | grep -q '"status":"ok"' && echo "✓ Health check passed"

echo "Testing auth..."
curl -sf -H "X-API-Key: test" http://localhost:8000/ | grep -q "Scristill" && echo "✓ Auth works"

echo "Cleaning up..."
docker stop $CONTAINER_ID > /dev/null
docker rm $CONTAINER_ID > /dev/null

echo ""
echo "✓ Docker build and runtime verified!"

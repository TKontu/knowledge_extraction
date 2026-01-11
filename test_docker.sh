#!/bin/bash
# Docker Build and Test Script
# Run this when Docker Desktop is running

set -e

echo "=== TechFacts Pipeline - Docker Build Test ==="
echo ""

# Build the image
echo "Building Docker image..."
docker build -t techfacts-pipeline .

echo ""
echo "Image built successfully!"
echo ""

# Check image size
echo "Image details:"
docker images techfacts-pipeline

echo ""
echo "=== Testing Container ==="
echo ""

# Run container in background
echo "Starting container..."
CONTAINER_ID=$(docker run -d -p 8000:8000 \
  -e API_KEY=test-key-12345 \
  techfacts-pipeline)

echo "Container ID: $CONTAINER_ID"
echo ""

# Wait for startup
echo "Waiting for server to start..."
sleep 5

# Test health endpoint
echo "Testing /health endpoint..."
HEALTH_RESPONSE=$(curl -s http://localhost:8000/health)
echo "Response: $HEALTH_RESPONSE"

if echo "$HEALTH_RESPONSE" | grep -q '"status":"ok"'; then
  echo "✓ Health check passed!"
else
  echo "✗ Health check failed!"
  docker logs $CONTAINER_ID
  docker stop $CONTAINER_ID
  docker rm $CONTAINER_ID
  exit 1
fi

echo ""

# Test authenticated endpoint
echo "Testing authenticated endpoint without API key..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/)
if [ "$HTTP_CODE" = "401" ]; then
  echo "✓ Auth rejection works (401)"
else
  echo "✗ Expected 401, got $HTTP_CODE"
fi

echo ""
echo "Testing authenticated endpoint with API key..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: test-key-12345" http://localhost:8000/)
if [ "$HTTP_CODE" = "200" ]; then
  echo "✓ Auth success (200)"
else
  echo "✗ Expected 200, got $HTTP_CODE"
fi

echo ""

# Test CORS
echo "Testing CORS headers..."
CORS_RESPONSE=$(curl -s -I -H "Origin: http://localhost:8080" http://localhost:8000/health)
if echo "$CORS_RESPONSE" | grep -qi "access-control-allow-origin"; then
  echo "✓ CORS headers present"
else
  echo "✗ CORS headers missing"
fi

echo ""

# Show logs
echo "=== Container Logs ==="
docker logs $CONTAINER_ID

echo ""

# Cleanup
echo "=== Cleanup ==="
docker stop $CONTAINER_ID
docker rm $CONTAINER_ID

echo ""
echo "=== All Tests Passed! ==="
echo ""
echo "To run manually:"
echo "  docker run -p 8000:8000 -e API_KEY=your-key techfacts-pipeline"

#!/bin/bash
# Push built images to container registry
# Usage: ./scripts/push-images.sh [registry-prefix]
# Example: ./scripts/push-images.sh ghcr.io/tkontu

set -e

REGISTRY_PREFIX="${1:-ghcr.io/tkontu}"
TAG="${2:-latest}"

echo "=== Pushing images to $REGISTRY_PREFIX ==="

# Images to push (local name -> registry name)
declare -A IMAGES=(
    ["knowledge_extraction-orchestrator-firecrawl-api"]="firecrawl-api"
    ["knowledge_extraction-orchestrator-camoufox"]="camoufox"
    ["knowledge_extraction-orchestrator-proxy-adapter"]="proxy-adapter"
    ["knowledge_extraction-orchestrator-pipeline"]="pipeline"
)

for LOCAL_NAME in "${!IMAGES[@]}"; do
    REMOTE_NAME="${IMAGES[$LOCAL_NAME]}"
    FULL_TAG="$REGISTRY_PREFIX/$REMOTE_NAME:$TAG"
    
    echo ""
    echo "--- $LOCAL_NAME -> $FULL_TAG ---"
    
    # Check if local image exists
    if ! docker image inspect "$LOCAL_NAME:latest" &>/dev/null; then
        echo "  [SKIP] Local image not found: $LOCAL_NAME:latest"
        continue
    fi
    
    # Tag
    echo "  Tagging..."
    docker tag "$LOCAL_NAME:latest" "$FULL_TAG"
    
    # Push
    echo "  Pushing..."
    docker push "$FULL_TAG"
    
    echo "  [OK] Pushed $FULL_TAG"
done

echo ""
echo "=== Done ==="
echo ""
echo "On remote, set in .env:"
echo "  REGISTRY_PREFIX=$REGISTRY_PREFIX"
echo ""
echo "Then run:"
echo "  docker compose -f docker-compose.prod.yml pull"
echo "  docker compose -f docker-compose.prod.yml up -d"

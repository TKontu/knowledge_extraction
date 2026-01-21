#!/bin/bash
# Build and push Docker images for knowledge extraction orchestrator
# Usage: ./build-and-push.sh [registry_prefix] [tag]
# Example: ./build-and-push.sh ghcr.io/tkontu v1.2.0

set -e

# Configuration
REGISTRY_PREFIX="${1:-ghcr.io/tkontu}"
TAG="${2:-latest}"

echo "🏗️  Building and pushing images..."
echo "📦 Registry: $REGISTRY_PREFIX"
echo "🏷️  Tag: $TAG"
echo ""

# Update Dockerfile cache buster
CACHE_BUST=$(date +%Y-%m-%d-%H%M%S)
echo "🔄 Updating cache buster: $CACHE_BUST"
sed -i "s/^ARG CACHE_BUST=.*/ARG CACHE_BUST=$CACHE_BUST/" Dockerfile

# Build and push function
build_and_push() {
    local service=$1
    local context=$2
    local dockerfile=$3
    local image_name="$REGISTRY_PREFIX/$service:$TAG"

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🔨 Building $service"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    docker build \
        --platform linux/amd64 \
        -t "$image_name" \
        -f "$dockerfile" \
        "$context"

    echo ""
    echo "📤 Pushing $image_name"
    docker push "$image_name"

    # Also tag as latest
    if [ "$TAG" != "latest" ]; then
        local latest_image="$REGISTRY_PREFIX/$service:latest"
        echo "🏷️  Tagging as latest: $latest_image"
        docker tag "$image_name" "$latest_image"
        docker push "$latest_image"
    fi

    echo "✅ $service complete"
}

# Build main services
build_and_push "pipeline" "." "Dockerfile"
build_and_push "camoufox" "." "Dockerfile.camoufox"
build_and_push "firecrawl-api" "./vendor/firecrawl/apps/api" "./vendor/firecrawl/apps/api/Dockerfile"
build_and_push "proxy-adapter" "." "Dockerfile.proxy"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ All images built and pushed successfully!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Images pushed:"
echo "  - $REGISTRY_PREFIX/pipeline:$TAG"
echo "  - $REGISTRY_PREFIX/camoufox:$TAG"
echo "  - $REGISTRY_PREFIX/firecrawl-api:$TAG"
echo "  - $REGISTRY_PREFIX/proxy-adapter:$TAG"
echo ""
echo "To deploy, update your docker-compose.prod.yml or use:"
echo "  export PIPELINE_TAG=$TAG"
echo "  export CAMOUFOX_TAG=$TAG"
echo "  export FIRECRAWL_TAG=$TAG"
echo "  docker compose -f docker-compose.prod.yml pull"
echo "  docker compose -f docker-compose.prod.yml up -d"

#!/bin/bash
# Playwright container entrypoint with transparent proxy via iptables
# Redirects HTTP/HTTPS traffic to proxy-adapter for anti-bot bypass

set -e

echo "=== Playwright Transparent Proxy Setup ==="

# Proxy adapter address (within Docker network)
PROXY_HOST="proxy-adapter"
PROXY_PORT="8192"

# Resolve proxy-adapter IP
PROXY_IP=$(getent hosts ${PROXY_HOST} | awk '{ print $1 }')

if [ -z "$PROXY_IP" ]; then
    echo "ERROR: Could not resolve ${PROXY_HOST}"
    exit 1
fi

echo "Proxy adapter: ${PROXY_HOST} (${PROXY_IP}:${PROXY_PORT})"

# Flush existing rules
echo "Flushing existing iptables rules..."
iptables -t nat -F

# Create iptables NAT rules for transparent proxying
# Redirect outbound HTTP (80) and HTTPS (443) to proxy-adapter

echo "Setting up iptables NAT rules..."

# Determine Docker network from proxy-adapter IP
# Extract the /16 subnet from proxy IP (e.g., 172.19.0.3 -> 172.19.0.0/16)
DOCKER_SUBNET=$(echo ${PROXY_IP} | awk -F. '{print $1"."$2".0.0/16"}')
echo "Docker network subnet: ${DOCKER_SUBNET}"

# Exclude internal Docker network and localhost from proxying
# 127.0.0.0/8      - Localhost
# ${DOCKER_SUBNET} - This container's Docker network

# Redirect HTTP (port 80) to proxy-adapter:8192
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -d ${DOCKER_SUBNET} -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 80 \
    -j DNAT --to-destination ${PROXY_IP}:${PROXY_PORT}

# Redirect HTTPS (port 443) to proxy-adapter:8192
# Note: HTTPS to blocked domains will be rejected by proxy (FlareSolverr limitation)
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -d ${DOCKER_SUBNET} -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 443 \
    -j DNAT --to-destination ${PROXY_IP}:${PROXY_PORT}

echo "iptables NAT rules configured:"
iptables -t nat -L OUTPUT -n -v

echo "=== Transparent Proxy Setup Complete ==="
echo ""
echo "Starting Playwright service..."
echo ""

# Execute original Playwright entrypoint or command
# The Playwright image uses node by default
exec "$@"

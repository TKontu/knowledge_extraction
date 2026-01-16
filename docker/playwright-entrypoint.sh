#!/bin/bash
# Playwright container entrypoint with optional transparent proxy via iptables
# Primary proxy method: PROXY_SERVER env var (explicit, works with IPv4/IPv6)
# Fallback: iptables NAT redirect (IPv4 only, requires NET_ADMIN capability)

set -e

echo "=== Playwright Proxy Setup ==="

# Check if explicit proxy is configured (preferred method)
if [ -n "$PROXY_SERVER" ]; then
    echo "Explicit proxy configured: ${PROXY_SERVER}"
    echo "Skipping iptables transparent proxy setup (not needed)"
    echo ""
    echo "Starting Playwright service..."
    echo ""
    exec "$@"
fi

# Fallback: transparent proxy via iptables (IPv4 only)
echo "No PROXY_SERVER env var - attempting transparent proxy setup..."

PROXY_HOST="proxy-adapter"
PROXY_PORT="8192"

# Resolve proxy-adapter IP
PROXY_IP=$(getent hosts ${PROXY_HOST} | awk '{ print $1 }')

if [ -z "$PROXY_IP" ]; then
    echo "WARNING: Could not resolve ${PROXY_HOST}"
    echo "Continuing without proxy..."
    exec "$@"
fi

echo "Proxy adapter: ${PROXY_HOST} (${PROXY_IP}:${PROXY_PORT})"

# Check if IPv6 (contains colon) - iptables NAT doesn't work well with IPv6
if [[ "$PROXY_IP" == *":"* ]]; then
    echo "WARNING: IPv6 address detected - iptables transparent proxy not supported"
    echo "Recommendation: Set PROXY_SERVER=http://proxy-adapter:8192 in environment"
    echo "Continuing without transparent proxy..."
    exec "$@"
fi

# IPv4 transparent proxy setup
echo "Setting up iptables NAT rules for transparent proxy..."

# Flush existing rules
iptables -t nat -F 2>/dev/null || {
    echo "WARNING: iptables not available (missing NET_ADMIN capability?)"
    echo "Continuing without transparent proxy..."
    exec "$@"
}

# Determine Docker network from proxy-adapter IP
# Extract the /16 subnet from proxy IP (e.g., 172.19.0.3 -> 172.19.0.0/16)
DOCKER_SUBNET=$(echo ${PROXY_IP} | awk -F. '{print $1"."$2".0.0/16"}')
echo "Docker network subnet: ${DOCKER_SUBNET}"

# Redirect HTTP (port 80) to proxy-adapter:8192
iptables -t nat -A OUTPUT -p tcp --dport 80 -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 80 -d ${DOCKER_SUBNET} -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 80 -j DNAT --to-destination ${PROXY_IP}:${PROXY_PORT}

# Redirect HTTPS (port 443) to proxy-adapter:8192
iptables -t nat -A OUTPUT -p tcp --dport 443 -d 127.0.0.0/8 -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 443 -d ${DOCKER_SUBNET} -j RETURN
iptables -t nat -A OUTPUT -p tcp --dport 443 -j DNAT --to-destination ${PROXY_IP}:${PROXY_PORT}

echo "iptables NAT rules configured:"
iptables -t nat -L OUTPUT -n -v

echo "=== Transparent Proxy Setup Complete ==="
echo ""
echo "Starting Playwright service..."
echo ""

exec "$@"

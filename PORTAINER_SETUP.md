# Portainer Environment Variables Configuration

## Required Secrets (Set in Portainer)

Add these 3 variables in your Portainer stack environment variables:

```bash
API_KEY=<your-secure-production-api-key-32plus-characters>
OPENAI_BASE_URL=http://<your-llm-server-ip>:9003/v1
OPENAI_EMBEDDING_BASE_URL=http://<your-embedding-server-ip>:9003/v1
```

**That's it.** Everything else is configured in `stack.env`.

---

## What's Already Configured in stack.env

These variables are committed in the repository and work automatically:

```bash
# LLM Configuration
OPENAI_API_KEY=ollama
LLM_MODEL=gemma3-12b-awq
RAG_EMBEDDING_MODEL=bge-large-en

# Proxy Configuration (enables Akamai bypass)
PLAYWRIGHT_PROXY_SERVER=http://proxy-adapter:8192

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

**Do not duplicate these in Portainer** - they're already set.

---

## Deployment Steps

1. In Portainer, edit your stack environment variables
2. Add only the 3 required secrets above
3. Deploy/redeploy the stack
4. Verify with: `docker exec <playwright-container> env | grep PROXY_SERVER`
   - Should output: `PROXY_SERVER=http://proxy-adapter:8192`

---

## Verification Commands

### Test WEG Crawl (Akamai-protected site):
```bash
curl -X POST http://192.168.0.136:8742/api/v1/crawl \
  -H "X-API-Key: <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "http://www.weg.net",
    "project_id": "<project-id>",
    "company": "WEG-Test",
    "max_depth": 1,
    "limit": 2
  }'
```

### Check Proxy Logs:
```bash
docker logs <proxy-adapter-container> | grep weg.net
```

**Expected output:**
```
proxy_routing method=flaresolverr url=http://www.weg.net
flaresolverr_request: solving challenge...
HTTP 200 OK
```

If you see this, the proxy integration is working correctly.

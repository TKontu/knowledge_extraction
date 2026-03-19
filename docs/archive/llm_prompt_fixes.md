# LLM Prompt Fixes - JSON Truncation Issue

## Problem Identified

The extraction pipeline was experiencing JSON truncation errors because:

1. **Unlimited fact extraction**: Prompt didn't limit the number of facts to extract
2. **Low token limit**: `max_tokens=4096` was insufficient for large pages
3. **No conciseness guidance**: LLM extracted verbose facts with long quotes

**Evidence from logs:**
```
content_length=15748 error at char 15657  (99% through - truncated at token limit)
content_length=17881 error at char 17850  (99% through - truncated at token limit)
content_length=13365 error at char 13365  (100% at end - truncated)
```

The LLM was trying to extract 50-100+ facts from large Wikipedia/book pages, running out of tokens mid-JSON response.

## Fixes Applied (Commit: 7ea2a99)

### 1. Strict Fact Limit
```python
# OLD: "Extract concrete, verifiable facts" (unlimited)
# NEW: "Extract the TOP 10 most important, concrete, verifiable facts"
```

### 2. Explicit Conciseness Constraints
```python
CRITICAL CONSTRAINTS:
- Extract MAXIMUM 10 facts (prioritize most important/unique information)
- Keep facts concise (1-2 sentences each)
- Keep source_quote brief (max 50 words)
- Must output valid, complete JSON - no truncation allowed
```

### 3. Increased Token Limit
```python
# config.py
llm_max_tokens: int = Field(
    default=8192,  # Increased from 4096
    ...
)
```

## Expected Impact

With 10 facts max and 8192 tokens:
- **Before**: Attempting 50-100 facts → 6000+ tokens needed → truncated at 4096 → JSON parse error
- **After**: Max 10 facts × ~400 tokens/fact = ~4000 tokens → fits comfortably in 8192 → complete JSON

**JSON parse success rate should improve from ~87% to ~99%+**

## Next Steps

### 1. Restart Pipeline Service

The changes require restarting the container to load new config:

```bash
# Restart pipeline container
docker compose restart pipeline

# Or rebuild if needed
docker compose up -d --build pipeline
```

### 2. Cancel Current Extraction Job

The current job (started at 09:22:04) is still using the old prompt/token limit:

```bash
# Option A: Wait for it to complete (~45 mins total)
# - It will finish with the old prompt (many JSON errors)
# - Data will commit when job completes

# Option B: Kill and restart (recommended)
# 1. Restart pipeline container (kills running job)
# 2. Start new extraction job with fixed prompt
```

### 3. Start Fresh Extraction

After restarting:

```python
# Via MCP
mcp__knowledge-extraction__extract_knowledge(
    project_id="19a92a22-dd92-430a-bd34-692792631b90"
)
```

## Monitoring Success

Look for these improvements in logs:

**Before (broken):**
```
warning context=extract_facts_direct error=Unterminated string starting at: line 355
warning event=json_repair_failed
```

**After (fixed):**
```
INF facts_extracted=10 event=llm_extraction_completed
INF extractions_created=10 event=extraction_completed
```

## Why Data Wasn't Persisting

The earlier investigation revealed:
- ✅ LLM was processing successfully
- ✅ Entities were being extracted
- ❌ **BUT**: Job hasn't committed yet (still running after 48 minutes)

**Root cause**: With 224 sources at ~2 min/source (10 concurrent), the job takes ~45 minutes. All data is buffered in the SQLAlchemy session (flushed but not committed) until `worker.process_job()` completes.

**Solution for visibility**: Consider implementing incremental commits (separate issue).

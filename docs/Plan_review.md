  Plan v3.1 Analysis: Gaps Against Actual Code

  Overall Assessment

  The plan correctly identifies all 7 root causes and proposes sound fixes for each. The defense-in-depth approach (classification → grounding → recalibration) is
  well-layered. The two agents work on non-overlapping files. However, there is one significant knowledge-loss gap the plan doesn't address, plus a few practical
  issues worth fixing before assigning agents.

  ---
  GAP 1 (High): Chunk size vs user prompt mismatch — silent content loss
  **STATUS: RESOLVED (Phase 2B, 2026-02-26)**
  **Fix: EXTRACTION_CONTENT_LIMIT = 20000 in schema_extractor.py and worker.py**

  The numbers:
  - chunk_document() at chunking.py:162 uses max_tokens=8000 where count_tokens = len(text) // 4, so max ~32,000 chars per chunk
  - _build_user_prompt() at schema_extractor.py:440 truncates to content[:8000] — only 8,000 chars
  - Result: up to 75% of each chunk is silently dropped before the LLM ever sees it

  Is this theoretical? No. For a typical product page with 15,000 chars of markdown:
  - count_tokens(15000) = 3750 — below the 8000-token threshold, so it stays as a single chunk
  - content[:8000] drops the last 7,000 chars — potentially half the product specs

  How much context budget is actually available?
  - Qwen3-30B context: ~32,768 tokens
  - llm_max_tokens (response): 8,192
  - System prompt: ~500 tokens (measured from the template)
  - Available for content: ~24,000 tokens ≈ 96,000 chars
  - Current usage: ~2,000 tokens (8,000 chars) = 8% utilization

  The plan preserves content[:8000] unchanged. This is the single biggest source of knowledge loss in the pipeline — not hallucination, but content never reaching
  the LLM at all.

  Recommendation: Align the two limits. Either:
  - Increase user prompt to content[:20000] (still only ~25% of context budget, safe for small models), OR
  - Reduce chunk_document(max_tokens=5000) to ~20K chars and use content[:20000]

  This change belongs to Agent B (schema_extractor.py), with a coordinating change to chunking.py (potentially Agent A territory, or keep in B since it's the
  extractor's concern).

  ---
  GAP 2 (Medium): Content cleaning only applies to classification, not to extraction input
  **STATUS: RESOLVED (Phase 2C, 2026-02-26)**
  **Fix: strip_structural_junk() applied in _build_user_prompt() before truncation**

  The plan explicitly states: "Applied ONLY in the classification path, NOT to extraction LLM input." This is a deliberate design choice, but consider:

  - With content[:8000], the first 8K chars are all the LLM gets
  - For a page where nav junk consumes the first 1,700 chars (median from the audit), that's 21% of the extraction window wasted on navigation
  - The Phase 2 grounding rules tell the LLM to ignore irrelevant content, but the LLM can't extract what isn't in its window

  This compounds with GAP 1: if we keep content[:8000], the effective content is only ~6,300 chars after nav junk. If we increase to content[:20000], nav junk
  becomes proportionally less impactful (~8.5%), and this gap becomes less urgent.

  Recommendation: If GAP 1 is fixed (larger content window), this gap is tolerable. If GAP 1 is NOT fixed, consider applying content cleaning to the extraction
  input too — at least the structural patterns from Layer 1. The line-density windowing (Layer 2) is classification-specific but the 4 universal patterns
  (empty-alt images, skip-to-content links, bare link lists, bare image lines) are safe to remove from extraction input as well.

  ---
  GAP 3 (Medium): onlyMainContent only affects future crawls

  The plan adds "onlyMainContent": True to scrape() and start_batch_scrape(). But:

  - The 11,582 existing pages in the database were scraped without this flag
  - Re-extraction (the verification step: "Re-extract David Brown Santasalo") uses stored content, not re-scraped content
  - The content cleaning (Phase 1E) only applies to the classification embedding path

  So re-extraction will still feed nav-contaminated content to the LLM. The grounding rules (Phase 2) partially mitigate this — the LLM is told to ignore
  irrelevant content — but it's not as effective as actually removing the junk.

  Recommendation: Acceptable as-is for existing data. The combination of classification (skips irrelevant pages) + grounding (LLM ignores junk) + confidence
  recalibration (sparse results get low scores) provides adequate protection. But for a definitive verification, re-scrape DBS with onlyMainContent: true before
  re-extracting.

  **NOTE (2026-02-26)**: With Phase 2C (Layer 1 cleaning on extraction input) now implemented, structural junk IS removed before
  the LLM sees it, even for existing pages. This significantly reduces GAP 3's impact — onlyMainContent is now a nice-to-have
  optimization rather than a critical fix.

  ---
  GAP 4 (Low): Spec bug in truncation logger
  **STATUS: RESOLVED (Phase 0B, 2026-02-26)**
  **Fix: original_length captured before truncation in embedding.py**

  Plan specifies:
  if len(text) > MAX_EMBED_CHARS:
      text = text[:MAX_EMBED_CHARS]
      logger.debug("embedding_text_truncated", original_length=len(text))

  After reassignment, len(text) is the truncated length. Need orig = len(text) before truncation.

  ---
  GAP 5 (Low): No minimum content quality gate

  Pages like "Access Denied", "403 Forbidden", or empty error pages with just 50 chars of text still flow through the full extraction pipeline. The classifier can
  skip by URL pattern (/404|/error) but not by content emptiness.

  - Grounding rules (Phase 2) cause the LLM to return null → confidence recalibration (3A) gives ≤0.1 → filtered by merge threshold 0.3
  - So it's harmless to the output, but wastes LLM calls

  Recommendation: Not worth adding complexity. The existing defense handles this adequately.

  ---
  Plan Strengths (Confirmed Correct)

  I verified these against the actual code:
  Phase: 0A
  Change: bge-m3 default
  Code Match: config.py:89 currently bge-large-en, dimension at embedding.py:70 = 1024 (matches bge-m3)
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 0B
  Change: Truncation safety
  Code Match: embedding.py:77 has no truncation. Both embed() and embed_batch() need it
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 1A
  Change: Enable classification
  Code Match: 4 booleans at config.py:389-434 all False. Wiring at schema_orchestrator.py:87-136 already works
  Status: **PENDING** — final increment, flip 4 booleans
  ────────────────────────────────────────
  Phase: 1B
  Change: prompt_hint in embeddings
  Code Match: smart_classifier.py:466-469 missing prompt_hint. Cache key at :454 uses SHA256 of group text, auto-invalidates
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 1C
  Change: Window 2000→6000
  Code Match: smart_classifier.py:510 and :299 both hardcode 2000
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 1D
  Change: Dynamic fallback
  Code Match: :250-266 currently returns [] (all groups). Plan's 80% threshold + min 2 is sound
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 1E
  Change: Content cleaning
  Code Match: NEW file. Validated on real data, 0.07% false positive
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 2A
  Change: Grounding rules
  Code Match: schema_extractor.py:353-362 currently says only "Use null for unknown values" — too weak
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 2A
  Change: Remove domain-specific lines
  Code Match: :407-408 hardcode "For locations" / "For products" — violates template-agnostic design
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 2B
  Change: Content window 8K→20K
  Code Match: schema_extractor.py:440 content[:8000], worker.py:405,464-466 content[:8000]
  Status: **IMPLEMENTED** ✓ — EXTRACTION_CONTENT_LIMIT = 20000 in both files
  ────────────────────────────────────────
  Phase: 2C
  Change: Layer 1 cleaning on extraction input
  Code Match: NEW — strip_structural_junk() before truncation in _build_user_prompt()
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 3A
  Change: Confidence recalibration
  Code Match: :173 falls back to 0.8. Plan's _is_empty_result() + ratio formula is mathematically sound
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 3B
  Change: Majority vote
  Code Match: :304 uses any(). Majority vote works correctly with _apply_defaults() converting null→False pre-merge
  Status: **IMPLEMENTED** ✓
  ────────────────────────────────────────
  Phase: 3C
  Change: None bypass fix
  Code Match: smart_merge.py:83 passes None. Fix is correct
  Status: **IMPLEMENTED** ✓
  ---
  Scenario Verification

  I traced three scenarios through the plan's changes:

  Hallucinated extraction (DBS careers page → manufacturing):
  1. Phase 1: URL /careers → skip_extraction=True → blocked (never reaches LLM)
  2. Even without URL match: smart classifier scores "manufacturing" low → dynamic fallback excludes it
  3. Even if extracted: 2/4 fields populated → confidence 0.7 × 0.75 = 0.525 → passes 0.3 threshold :(
  4. But: Phase 2 grounding causes LLM to return null if content isn't about manufacturing → confidence ≤ 0.1 → filtered

  Layers 1-2 prevent most hallucinations. Layer 3 catches stragglers. Layer 4 is the final safety net. Defense-in-depth works.

  Empty extraction that slips through:
  - All null → defaults applied → populated_ratio = 0.0 → confidence capped at 0.1 → filtered by 0.3 threshold. Clean.

  Good extraction from relevant page:
  - 3/5 fields populated → ratio 0.6 → confidence 0.8 × 0.8 = 0.64. Passes threshold. Clean.

  ---
  Implementation Progress (2026-02-26)

  All phases implemented except Phase 1A (enable classification). 158 related tests pass.

  Remaining:
  - Phase 1A: Flip 4 config booleans to True
  - Verification: Re-extract David Brown Santasalo
  - Commit changes

Extraction Pipeline Architecture Assessment                                                                                 
                                                                                                                              
  The Good                                                                                                                    
                                                                                                                              
  Anti-hallucination is solid:                                                                                                
  - System prompt explicitly says "Extract ONLY from content provided. Do NOT use outside knowledge"                          
  - Temperature 0.1 (very deterministic), only bumps +0.05 per retry                                                          
  - Boolean fields default to false unless explicit evidence found                                                            
  - Empty results auto-penalized: if <20% of fields populated, confidence capped at 0.1                                       
  - Null required for any field without evidence                                                                              
                                                                                                                              
  Classification fallbacks are conservative:                                                                                  
  - If embedding server fails → extract ALL field groups (no content lost)                                                    
  - If confidence < 0.4 → still extracts top 2 groups                                                                         
  - Skip patterns are narrow: only careers, legal, login, sitemap — NOT news/blog/press

  Data preservation:
  - source.content is never modified — cleaned_content is separate
  - Extraction dedup threshold is 0.90 (near-identical only)

  The Risks

  1. Content Truncation — Front-biased, 20K chars

  cleaned[:EXTRACTION_CONTENT_LIMIT]  # Just chops at position 20000
  Covers ~90% of pages fully. But for long pages, everything after 20K chars is silently dropped. No smart splitting — if
  important specs are at the bottom of a long page, they're lost.

  2. Boilerplate Over-Removal — Legitimate Repeated Content

  The 70% threshold means any block appearing on >70% of a domain's pages is removed. Product specs, standard technical
  tables, or recurring certifications that legitimately appear across many pages would be stripped. No semantic check — it's
  purely frequency-based.

  3. Multi-Chunk Merge Logic — Conflict Resolution is Naive

  When a page is chunked and extracted separately:
  - Integers/floats: takes maximum — if chunk 1 says "Founded 2020" and chunk 2 says "Founded 1990", you get 1990... or 2020,
  depending on which is "max"
  - Text fields: concatenated with "; " — creates messy multi-value strings
  - Booleans: majority vote — works fine
  - No conflict detection or flagging

  4. No Source Citation in Extractions

  The LLM is told to extract facts but never asked to quote the source text. This means:
  - No way to verify an extraction against the original
  - Subtle hallucinations (plausible but wrong values) are undetectable without manual review
  - No traceability from extracted fact back to specific passage

  5. No Post-Extraction Validation

  A FactValidator exists in the codebase but isn't wired into the schema extraction pipeline. There's no cross-reference
  checking, no sanity bounds on numeric fields, no entity resolution across sources.

  Risk Matrix

  ┌────────────────────────────────────┬────────────┬───────────────────┬─────────────────────────┐
  │                Risk                │  Severity  │     Frequency     │   Mitigation Exists?    │
  ├────────────────────────────────────┼────────────┼───────────────────┼─────────────────────────┤
  │ Long page truncation               │ Medium     │ ~10% of pages     │ No — naive chop         │
  ├────────────────────────────────────┼────────────┼───────────────────┼─────────────────────────┤
  │ Boilerplate removes real specs     │ Medium     │ Domain-specific   │ Threshold configurable  │
  ├────────────────────────────────────┼────────────┼───────────────────┼─────────────────────────┤
  │ Chunk merge conflicts              │ Low-Medium │ Multi-chunk pages │ No conflict detection   │
  ├────────────────────────────────────┼────────────┼───────────────────┼─────────────────────────┤
  │ Subtle LLM hallucinations          │ Medium     │ Unknown           │ No quote/citation       │
  ├────────────────────────────────────┼────────────┼───────────────────┼─────────────────────────┤
  │ Missing post-extraction validation │ Medium     │ All extractions   │ Validator exists unused │
  └────────────────────────────────────┴────────────┴───────────────────┴─────────────────────────┘

  What I'd Prioritize

  High impact, moderate effort:
  1. Add source quoting — Ask LLM to include "source_quote" per field. Makes hallucination detectable and builds trust in
  results
  2. Smarter truncation — Split long pages into overlapping windows instead of chopping at 20K

  Medium impact, low effort:
  3. Conflict detection in merge — Flag when chunks disagree on the same field instead of silently resolving
  4. Wire up the FactValidator — It already exists, just needs integration

  Worth monitoring:
  5. Boilerplate threshold per-domain — Some domains with heavy product catalogs may need higher thresholds
# TODO: Quoting Quality Trials

**Goal**: Achieve >85% verbatim quote accuracy across all field types before re-extracting ~33K records. Validate across diverse domains, not just drivetrain companies.

**Date**: 2026-03-06

---

## Problem Statement

Current extraction prompts produce `_quotes` that are only **70% verbatim** on average. Numeric and boolean fields are worst at 44-50%. The model paraphrases or fabricates quote text instead of copying from the source.

### Current Quote Quality (500 random samples, main batch)

| Group | Field | Samples | Verbatim | % |
|-------|-------|--------:|:--------:|----:|
| company_info | employee_count_range | 18 | 8 | 44% |
| company_info | employee_count | 10 | 5 | 50% |
| manufacturing | manufactures_motors | 20 | 9 | 45% |
| manufacturing | manufactures_drivetrain_accessories | 17 | 10 | 59% |
| manufacturing | manufacturing_details | 42 | 25 | 60% |
| company_info | headquarters_location | 92 | 56 | 61% |
| company_info | number_of_sites | 19 | 11 | 58% |
| manufacturing | manufactures_gearboxes | 20 | 13 | 65% |
| company_meta | certifications | 18 | 12 | 67% |
| company_meta | locations | 50 | 36 | 72% |
| services | service_types | 23 | 17 | 74% |
| services | provides_services | 33 | 27 | 82% |
| company_info | company_name | 105 | 95 | 91% |
| services | services_gearboxes | 13 | 12 | 92% |

**Failure modes observed**:
- Model composes sentences instead of copying (e.g., "Bilfinger is a global company with over 500 employees" — not in source)
- Boolean field quotes are paraphrased summaries, not excerpts
- Numeric field quotes embed the number in a generated sentence

---

## Configuration

- **Model**: `Qwen3-30B-A3B-Instruct-4bit` (deployed, confirmed)
- **LLM endpoint**: `192.168.0.247:9003/v1`
- **Content limit**: 20,000 chars
- **Source quoting**: enabled (`extraction_source_quoting_enabled=True`)
- **Temperature**: 0.0 base

---

## Test Datasets

Three separate projects to validate quoting across different domains and field types.

### Dataset 1 — Drivetrain Companies (existing)

- **Project**: main batch `99a19141-9268-40a8-bc9e-ad1fa12243da`
- **Sources**: 20 existing sources selected for diversity
- **Template**: existing drivetrain schema (company_info, services, manufacturing, company_meta)
- **Field type mix**: strings, booleans, integers, lists, enums
- **Purpose**: Controlled comparison against known baseline

### Dataset 2 — Wikipedia Articles (new project)

- **Source URL**: `https://en.wikipedia.org/wiki/Special:Random` (scrape 15-20 random articles)
- **Purpose**: Test quoting on diverse, fact-dense content with varied topics
- **Template**: General knowledge / article facts

```yaml
name: "wikipedia_articles"
field_groups:
  - name: "article_info"
    description: "Basic article information"
    fields:
      - name: "title"
        field_type: "string"
        description: "Article title / subject name"
        required: true
      - name: "category"
        field_type: "string"
        description: "Primary topic category (person, place, event, concept, organism, object)"
      - name: "summary"
        field_type: "text"
        description: "One-sentence summary of the subject"
      - name: "inception_date"
        field_type: "string"
        description: "Date of birth, founding, creation, or first occurrence"
      - name: "location"
        field_type: "string"
        description: "Primary location, country, or geographic context"
      - name: "population_or_count"
        field_type: "integer"
        description: "Population, membership count, or other primary numeric measure"

  - name: "key_facts"
    description: "Key factual claims from the article"
    fields:
      - name: "notable_for"
        field_type: "string"
        description: "What the subject is best known for"
      - name: "related_entities"
        field_type: "list"
        description: "Other notable people, places, or things mentioned"
      - name: "dimensions_or_measurements"
        field_type: "string"
        description: "Any physical measurements, sizes, distances, or quantities mentioned"
      - name: "time_period"
        field_type: "string"
        description: "Primary time period or era the article covers"
      - name: "is_active"
        field_type: "boolean"
        description: "Whether the subject currently exists / is alive / is active"
      - name: "official_website"
        field_type: "string"
        description: "Official website URL if mentioned"
```

**Why Wikipedia**: Dense factual content, precise numbers, dates, and proper nouns that are easy to verify verbatim. Tests whether the model can quote facts like "population of 1,432,710" rather than paraphrasing to "a city with over a million residents".

### Dataset 3 — Job Listings (new project)

- **Source URL**: `https://www.monster.com/jobs/q-engineering-jobs?so=p.h.p&where=Remote` (scrape 15-20 job listing pages)
- **Purpose**: Test quoting on structured, repetitive content with specific requirements
- **Template**: Job listing extraction

```yaml
name: "job_listings"
field_groups:
  - name: "job_overview"
    description: "Core job listing information"
    fields:
      - name: "job_title"
        field_type: "string"
        description: "Exact job title as posted"
        required: true
      - name: "company_name"
        field_type: "string"
        description: "Hiring company name"
        required: true
      - name: "location"
        field_type: "string"
        description: "Job location (city, state, or Remote)"
      - name: "salary_range"
        field_type: "string"
        description: "Salary or compensation range if stated"
      - name: "employment_type"
        field_type: "enum"
        description: "Type of employment"
        enum_values: ["full-time", "part-time", "contract", "internship", "temporary"]
      - name: "experience_years"
        field_type: "integer"
        description: "Minimum years of experience required"
      - name: "is_remote"
        field_type: "boolean"
        description: "Whether the position is remote or allows remote work"

  - name: "job_requirements"
    description: "Skills and qualifications required"
    is_entity_list: true
    fields:
      - name: "name"
        field_type: "string"
        description: "Skill, technology, or qualification name"
      - name: "importance"
        field_type: "enum"
        description: "Whether required or preferred"
        enum_values: ["required", "preferred", "nice-to-have"]

  - name: "job_benefits"
    description: "Compensation and benefits information"
    fields:
      - name: "benefits_list"
        field_type: "list"
        description: "Listed benefits (health insurance, 401k, PTO, etc.)"
      - name: "has_equity"
        field_type: "boolean"
        description: "Whether stock options or equity are offered"
      - name: "has_remote_flexibility"
        field_type: "boolean"
        description: "Whether flexible/hybrid remote work is mentioned"
      - name: "education_requirement"
        field_type: "string"
        description: "Minimum education level required (e.g., Bachelor's, Master's)"
```

**Why job listings**: Highly structured content with precise requirements (exact salary numbers, specific years of experience, named technologies). Tests quoting on content where paraphrasing is clearly wrong — "5+ years of Python experience" should never become "extensive Python background".

---

## Trial Design

### Common Setup

- **Evaluation metric**: Per-field verbatim match rate (quote substring found in source content, case-insensitive)
- **Evaluation method**: SQL `content ILIKE '%' || quote_text || '%'` against source content
- **Success threshold**: >85% verbatim across all field types, no field type below 70%
- **All trials run on all 3 datasets** to ensure prompt changes generalize

### Trial A — Baseline (current prompt)

**Purpose**: Establish controlled baseline across all 3 datasets.

**Prompt** (current, non-strict):
```
Include a "_quotes" object mapping each non-null field to a brief verbatim
excerpt (15-50 chars) from the source that supports the value.
Example: "_quotes": {"field_name": "exact text from source"}
```

**What to measure**:
- Overall verbatim % per dataset
- Per-field verbatim %
- Per-field average quote length
- Count of null fields (data loss indicator)

### Trial B — Strict quoting as default

**Purpose**: Test if the existing `strict_quoting=True` prompt improves verbatim rates without causing data loss.

**Change**: Use the strict prompt on the first attempt (currently only used on retries).

**Prompt**:
```
CRITICAL QUOTING REQUIREMENT:
Include a "_quotes" object mapping each non-null field to an EXACT verbatim
excerpt (15-50 chars) copied directly from the source text.
The quote MUST appear word-for-word in the source content. Do NOT paraphrase,
translate, or fabricate quotes.
If you cannot find an exact quote in the source for a field, set that field
to null rather than inventing a quote.
Example: "_quotes": {"field_name": "exact text copied from source"}
```

**Risk**: The instruction "set that field to null rather than inventing a quote" may cause the model to drop valid fields it has trouble quoting. Measure null rate vs baseline.

**What to measure**:
- Verbatim % improvement vs Trial A
- Null field count increase vs Trial A (data loss)
- Per-field breakdown per dataset

### Trial C — Shorter quote window

**Purpose**: Shorter quotes are easier to copy verbatim. Test if reducing the length range helps accuracy.

**Change**: Modify quote length from "15-50 chars" to "10-30 chars".

**Prompt modification**:
```
Include a "_quotes" object mapping each non-null field to an EXACT verbatim
excerpt (10-30 chars) copied directly from the source text.
The quote MUST appear word-for-word in the source content.
Example: "_quotes": {"employee_count": "over 30,000 employees"}
```

**What to measure**:
- Verbatim % vs Trial A and B
- Whether shorter quotes are still useful for grounding verification
- Null field rate

### Trial D — Anti-paraphrase emphasis

**Purpose**: Explicitly call out the failure mode (paraphrasing) and show bad vs good examples.

**Prompt modification**:
```
QUOTING RULES:
For each non-null field, include a "_quotes" object with an exact copy-paste
excerpt (10-30 chars) from the source text above.

WRONG (paraphrased): "The company has manufacturing facilities worldwide"
RIGHT (verbatim copy): "manufacturing facilities in 12 countries"

The quote must be a substring that appears EXACTLY in the source. Copy it
character-for-character. Never rewrite, summarize, or compose a new sentence.
```

**What to measure**:
- Verbatim % vs all previous trials
- Whether explicit bad/good examples reduce paraphrasing
- Null field rate

---

## Execution Steps

### Phase 0: Project & Data Setup
- [ ] Create Wikipedia project via API with article_info + key_facts template
- [ ] Scrape 15-20 random Wikipedia articles (`https://en.wikipedia.org/wiki/Special:Random`)
- [ ] Create Jobs project via API with job_overview + job_requirements + job_benefits template
- [ ] Scrape 15-20 job listings from Monster.com (`https://www.monster.com/jobs/q-engineering-jobs?so=p.h.p&where=Remote`)
- [ ] Verify all sources have content stored

### Phase 1: Sample Selection (Drivetrain)
- [ ] Query 20 diverse sources from main batch (mix of extraction types, content lengths)
- [ ] Store source IDs for consistent comparison across trials
- [ ] Verify sources have content available

### Phase 2: Trial Runs (all 3 datasets x 4 trials = 12 runs)
- [ ] **Trial A**: Run extraction with current prompt on all 3 datasets
- [ ] **Trial B**: Run extraction with strict_quoting=True on all 3 datasets
- [ ] **Trial C**: Run extraction with shorter quote window on all 3 datasets
- [ ] **Trial D**: Run extraction with anti-paraphrase prompt on all 3 datasets

### Phase 3: Evaluation
- [ ] Compute per-field verbatim % for each trial x dataset combination
- [ ] Compute null field rates for each combination (data loss)
- [ ] Cross-dataset comparison: does the winning prompt generalize?
- [ ] Document findings in this file

### Phase 4: Implementation
- [ ] Apply winning prompt changes to `schema_extractor.py`
- [ ] Run tests to verify no regressions
- [ ] Deploy updated code
- [ ] Run full re-extraction of ~33K drivetrain sources

---

## Decision Criteria

| Metric | Minimum | Target |
|--------|---------|--------|
| Overall verbatim % (each dataset) | 80% | >85% |
| Worst field verbatim % | 60% | >70% |
| Null field increase vs baseline | <15% | <5% |
| Cross-dataset consistency | Within 10% of mean | Within 5% |
| Quote usefulness for grounding | Maintains current grounding precision | Improves it |

If no single trial meets all criteria, combine the best elements (e.g., Trial B's strictness + Trial D's examples + Trial C's length).

---

## Files to Modify

- `src/services/extraction/schema_extractor.py` — `_build_system_prompt()` and `_build_entity_list_system_prompt()` (lines 379-524)
- No config changes needed (quoting is already enabled)

## Dependencies

- Wikipedia + Monster scraping must complete before trials begin (Phase 0)
- Drivetrain trials use existing sources (no scraping needed)
- Re-extraction depends on trial completion

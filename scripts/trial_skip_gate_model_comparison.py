#!/usr/bin/env python3
"""Trial: Compare skip-gate models (gemma3-4B vs Qwen3-30B) on production data.

Samples random sources, runs both models with the same binary skip-gate prompt,
and evaluates against ground truth derived from extraction results.

Ground truth:
  - "extract" if the source has ≥1 extraction with avg confidence ≥ 0.3
  - "skip" if all extractions have avg confidence < 0.3 or no extractions exist

This GT is noisy (some pages may have relevant data the extractor missed),
so we also report examples for human review.

Usage:
    .venv/bin/python scripts/trial_skip_gate_model_comparison.py [--limit 100]
"""

import json
import random
import sys
import time
from dataclasses import dataclass
from uuid import UUID

sys.path.insert(0, "src")

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Source

# ── Config ──

VLLM_BASE = "http://192.168.0.247:9003/v1"
MODELS = ["gemma3-4B", "Qwen3-30B-A3B-it-4bit"]
PROJECT_ID = UUID("99a19141-9268-40a8-bc9e-ad1fa12243da")  # Drivetrain
CONTENT_LIMIT = 2000
CONFIDENCE_THRESHOLD = 0.3  # GT: page is "extract" if any extraction above this

SYSTEM_PROMPT = """You classify web pages for a structured data extraction pipeline.

You receive an extraction schema describing target data types, plus a web page.

Decision rules:
- "extract" = page contains data matching ANY field group in the schema
- "skip" = page has NO matching data (wrong industry, empty, navigation-only,
  login, job listings, holiday notices, legal/privacy, forum index)

When genuinely uncertain, prefer "extract" — missing data costs more than
a wasted extraction call.

Output JSON only: {"decision": "extract" or "skip"}"""

# Schema summary for drivetrain template (auto-generated from field groups)
SCHEMA_SUMMARY = """Data domain: company documentation
Entity type: Company

Target field groups:
  company_info: Basic company information
    Key fields: company_name, headquarters_location, employee_count_range
  manufacturing: Manufacturing capabilities and products
    Key fields: manufactures_gearboxes, manufactures_drivetrain_accessories
  services: Service offerings
    Key fields: provides_services, service_types
  products: Product catalog
    Key fields: product_name, series_name, torque_rating_nm"""

USER_TEMPLATE = """EXTRACTION SCHEMA:
{schema_summary}

PAGE:
URL: {url}
Title: {title}

Content:
{content}

Should this page be extracted or skipped? JSON only:"""


@dataclass
class PageSample:
    source_id: UUID
    url: str
    title: str | None
    content: str
    gt_decision: str  # "extract" or "skip"
    gt_confidence: float  # avg confidence of best extraction
    extraction_count: int
    source_group: str


@dataclass
class ModelResult:
    model: str
    decision: str
    raw_response: str
    latency: float
    tokens: int


def parse_decision(text: str) -> str:
    """Parse LLM response into 'extract' or 'skip'. Default: 'extract'."""
    text = text.strip()
    # Handle thinking tags
    if "<think>" in text:
        idx = text.rfind("</think>")
        if idx != -1:
            text = text[idx + 8 :].strip()
    # Handle markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0].strip()
    # Try JSON
    try:
        d = json.loads(text)
        if isinstance(d, dict) and d.get("decision", "").lower().strip() == "skip":
            return "skip"
        return "extract"
    except (json.JSONDecodeError, AttributeError):
        pass
    # Keyword fallback
    if '"skip"' in text.lower() or "'skip'" in text.lower():
        return "skip"
    return "extract"


def classify_page_sync(
    client: httpx.Client,
    model: str,
    url: str,
    title: str | None,
    content: str,
    timeout: float = 60.0,
) -> ModelResult:
    """Call vLLM to classify a single page (synchronous).

    Uses sync client to avoid async complexity with model swaps.
    First call after a model swap may take 60-120s (cold load).
    """
    user_msg = USER_TEMPLATE.format(
        schema_summary=SCHEMA_SUMMARY,
        url=url,
        title=title or "(no title)",
        content=content[:CONTENT_LIMIT],
    )

    start = time.monotonic()
    try:
        resp = client.post(
            f"{VLLM_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.0,
                "max_tokens": 100,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        raw = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)
        latency = time.monotonic() - start
        decision = parse_decision(raw)
        return ModelResult(model, decision, raw[:200], latency, tokens)
    except Exception as e:
        latency = time.monotonic() - start
        return ModelResult(model, "extract", f"ERROR: {e}", latency, 0)


def load_samples(limit: int) -> list[PageSample]:
    """Load random source pages with GT from extraction results."""
    with Session(engine) as session:
        # Get sources that have been extracted (v2) with content
        # Subquery: best avg confidence per source
        ext_stats = (
            select(
                Extraction.source_id,
                func.count(Extraction.id).label("ext_count"),
                func.max(Extraction.confidence).label("max_conf"),
            )
            .where(Extraction.project_id == PROJECT_ID)
            .where(Extraction.data_version == 2)
            .group_by(Extraction.source_id)
            .subquery()
        )

        query = (
            select(
                Source.id,
                Source.uri,
                Source.title,
                Source.content,
                Source.cleaned_content,
                Source.source_group,
                ext_stats.c.ext_count,
                ext_stats.c.max_conf,
            )
            .outerjoin(ext_stats, Source.id == ext_stats.c.source_id)
            .where(Source.project_id == PROJECT_ID)
            .where(Source.content.isnot(None))
            .where(func.length(Source.content) > 100)
        )

        rows = session.execute(query).all()
        print(f"Total sources with content: {len(rows)}")

        # Also include some sources with NO extractions (true skips)
        samples = []
        for row in rows:
            content = row.cleaned_content or row.content
            ext_count = row.ext_count or 0
            max_conf = float(row.max_conf) if row.max_conf else 0.0

            if ext_count > 0 and max_conf >= CONFIDENCE_THRESHOLD:
                gt = "extract"
            else:
                gt = "skip"

            samples.append(
                PageSample(
                    source_id=row.id,
                    url=row.uri or "",
                    title=row.title,
                    content=content,
                    gt_decision=gt,
                    gt_confidence=max_conf,
                    extraction_count=ext_count,
                    source_group=row.source_group or "",
                )
            )

        # Stratified sample: ensure mix of extract/skip
        extracts = [s for s in samples if s.gt_decision == "extract"]
        skips = [s for s in samples if s.gt_decision == "skip"]
        random.seed(42)
        random.shuffle(extracts)
        random.shuffle(skips)

        # Take proportional sample, ensure ≥20% of each class
        n_extract = max(int(limit * 0.6), min(len(extracts), limit - 20))
        n_skip = limit - n_extract
        n_extract = min(n_extract, len(extracts))
        n_skip = min(n_skip, len(skips))

        selected = extracts[:n_extract] + skips[:n_skip]
        random.shuffle(selected)

        print(
            f"Selected {len(selected)} samples: {n_extract} extract, {n_skip} skip (GT)"
        )
        return selected


def warm_up_model(client: httpx.Client, model: str) -> None:
    """Send a throwaway request to trigger model loading (cold start ~60-90s)."""
    print(f"  Loading {model} (cold start may take 60-90s)...", flush=True)
    start = time.monotonic()
    try:
        resp = client.post(
            f"{VLLM_BASE}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Say hi"}],
                "temperature": 0.0,
                "max_tokens": 5,
            },
            timeout=300.0,
        )
        lat = time.monotonic() - start
        print(f"  {model} ready ({lat:.1f}s)", flush=True)
    except Exception as e:
        lat = time.monotonic() - start
        print(f"  {model} warmup failed ({lat:.1f}s): {e}", flush=True)


def run_trial(
    samples: list[PageSample],
) -> dict[str, list[tuple[PageSample, ModelResult]]]:
    """Run models sequentially on all samples.

    Runs one model at a time on ALL samples to avoid constant model swapping.
    Each model is warmed up once before its batch.
    """
    results: dict[str, list[tuple[PageSample, ModelResult]]] = {m: [] for m in MODELS}

    with httpx.Client() as client:
        for model in MODELS:
            warm_up_model(client, model)

            errors = 0
            for i, sample in enumerate(samples):
                if (i + 1) % 20 == 0 or i == 0:
                    print(f"  {model}: {i + 1}/{len(samples)}...", flush=True)

                mr = classify_page_sync(
                    client, model, sample.url, sample.title, sample.content
                )
                results[model].append((sample, mr))

                if "ERROR" in mr.raw_response:
                    errors += 1
                    if errors >= 3:
                        print(f"  {model}: 3+ errors, aborting", flush=True)
                        # Fill remaining with "extract" (safe default)
                        for j in range(i + 1, len(samples)):
                            results[model].append(
                                (
                                    samples[j],
                                    ModelResult(model, "extract", "SKIPPED", 0.0, 0),
                                )
                            )
                        break

            print(
                f"  {model}: done ({len(results[model])} pages, {errors} errors)",
                flush=True,
            )
            print(flush=True)

    return results


def analyze_results(results: dict[str, list[tuple[PageSample, ModelResult]]]):
    """Compute metrics and print comparison."""
    print(f"\n{'=' * 80}")
    print("SKIP-GATE MODEL COMPARISON")
    print(f"{'=' * 80}\n")

    for model in MODELS:
        pairs = results[model]
        tp = fp = tn = fn = 0
        total_latency = 0.0
        total_tokens = 0
        false_negatives = []
        false_positives = []

        for sample, mr in pairs:
            total_latency += mr.latency
            total_tokens += mr.tokens

            if sample.gt_decision == "extract":
                if mr.decision == "extract":
                    tp += 1
                else:
                    fn += 1
                    false_negatives.append((sample, mr))
            else:  # gt = skip
                if mr.decision == "skip":
                    tn += 1
                else:
                    fp += 1
                    false_positives.append((sample, mr))

        total = tp + fp + tn + fn
        accuracy = (tp + tn) / total if total else 0
        precision = tp / (tp + fp) if (tp + fp) else 0
        recall = tp / (tp + fn) if (tp + fn) else 0
        f1 = (
            2 * precision * recall / (precision + recall) if (precision + recall) else 0
        )
        skip_precision = tn / (tn + fn) if (tn + fn) else 0
        avg_latency = total_latency / total if total else 0
        avg_tokens = total_tokens / total if total else 0

        print(f"── {model} ──")
        print(f"  Accuracy:       {accuracy:.1%} ({tp + tn}/{total})")
        print(f"  Precision:      {precision:.1%} (extract)")
        print(f"  Recall:         {recall:.1%} (extract) ← CRITICAL: must be ≥90%")
        print(f"  F1:             {f1:.1%}")
        print(
            f"  Skip precision: {skip_precision:.1%} (when it says skip, is it right?)"
        )
        print(f"  Avg latency:    {avg_latency:.2f}s/page")
        print(f"  Avg tokens:     {avg_tokens:.0f}/page")
        print(f"  Confusion:      TP={tp} FP={fp} TN={tn} FN={fn}")
        print()

        if false_negatives:
            print(
                f"  FALSE NEGATIVES ({len(false_negatives)}) — pages with data, model said 'skip':"
            )
            for sample, mr in false_negatives[:8]:
                print(f"    {sample.source_group}: {sample.url[:80]}")
                print(
                    f"      GT conf={sample.gt_confidence:.2f}, extractions={sample.extraction_count}"
                )
                print(f"      LLM: {mr.raw_response[:120]}")
                print()

        if false_positives[:3]:
            print(
                f"  FALSE POSITIVES ({len(false_positives)}) — no useful data, model said 'extract':"
            )
            for sample, mr in false_positives[:3]:
                print(f"    {sample.source_group}: {sample.url[:80]}")
                print(
                    f"      GT conf={sample.gt_confidence:.2f}, extractions={sample.extraction_count}"
                )
                print()

        print()

    # Head-to-head comparison
    print(f"{'=' * 80}")
    print("HEAD-TO-HEAD COMPARISON")
    print(f"{'=' * 80}\n")

    disagreements = []
    for (s1, m1), (s2, m2) in zip(results[MODELS[0]], results[MODELS[1]]):
        if m1.decision != m2.decision:
            disagreements.append((s1, m1, m2))

    print(f"Disagreements: {len(disagreements)}/{len(results[MODELS[0]])}")
    print()

    for sample, m1, m2 in disagreements[:15]:
        winner = "—"
        if sample.gt_decision == m1.decision:
            winner = MODELS[0]
        elif sample.gt_decision == m2.decision:
            winner = MODELS[1]

        print(f"  {sample.source_group}: {sample.url[:70]}")
        print(
            f"    GT={sample.gt_decision} (conf={sample.gt_confidence:.2f}, exts={sample.extraction_count})"
        )
        print(f"    {MODELS[0]:30s}: {m1.decision}")
        print(f"    {MODELS[1]:30s}: {m2.decision}")
        print(f"    Correct: {winner}")
        print()


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()

    print(f"\n{'=' * 80}")
    print("TRIAL: Skip-Gate Model Comparison")
    print(f"Models: {', '.join(MODELS)}")
    print(f"{'=' * 80}\n")

    samples = load_samples(args.limit)
    if not samples:
        print("No samples found!")
        return

    results = run_trial(samples)
    analyze_results(results)

    print(f"{'=' * 80}")
    print("TRIAL COMPLETE")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()

"""Capture repeatable search-regression results from the local Search AI admin API.

One sequential request is sent per case. The script does not retry failed calls.
Full result payloads are written locally; keep the output directory out of Git.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = BACKEND_ROOT / "evaluation" / "search-regression-cases.json"
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / "evaluation" / "results"
SEARCH_PATH = "/api/admin/platform/search"
VALID_CATEGORIES = {"people", "jobs", "articles"}

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env", override=False)
except ImportError:
    # Environment variables can also be supplied directly by the shell.
    pass


def load_cases(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"Cases file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cases file is not valid JSON: {exc}") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("Cases file must contain an object with a 'cases' list.")

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, case in enumerate(payload["cases"], start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {position} must be a JSON object.")

        case_id = str(case.get("id", "")).strip()
        category = str(case.get("category", "")).strip().lower()
        query = str(case.get("query", "")).strip()
        if not case_id or not query or category not in VALID_CATEGORIES:
            raise ValueError(
                f"Case {position} needs an id, a query, and category "
                f"one of {sorted(VALID_CATEGORIES)}."
            )
        if case_id in seen_ids:
            raise ValueError(f"Duplicate case id: {case_id}")
        if len(query) > 500:
            raise ValueError(f"Case '{case_id}' query exceeds the API limit of 500 characters.")

        filters = case.get("filters", [])
        if not isinstance(filters, list) or any(not isinstance(item, dict) for item in filters):
            raise ValueError(f"Case '{case_id}': filters must be a list of JSON objects.")

        seen_ids.add(case_id)
        validated.append({
            "id": case_id,
            "category": category,
            "scenario": str(case.get("scenario", "")).strip(),
            "query": query,
            "filters": filters,
            "notes": str(case.get("notes", "")).strip(),
        })
    if not validated:
        raise ValueError("The cases file does not contain any search cases.")
    return validated


def call_search(
    base_url: str,
    token: str,
    case: dict[str, Any],
    *,
    page_size: int,
    timeout: float,
) -> dict[str, Any]:
    body = {
        "query": case["query"],
        "category": case["category"],
        "page": 1,
        "page_size": page_size,
        "filters": case["filters"],
    }
    request = Request(
        base_url.rstrip("/") + SEARCH_PATH,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Admin search returned HTTP {exc.code}.") from None
    except URLError as exc:
        reason = getattr(exc, "reason", None)
        reason_name = type(reason).__name__ if reason is not None else "connection error"
        raise RuntimeError(f"Unable to reach the Search AI API ({reason_name}).") from None
    except TimeoutError:
        raise RuntimeError(f"Search request timed out after {timeout:g} seconds.") from None
    except json.JSONDecodeError:
        raise RuntimeError("Search API returned a response that was not valid JSON.") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError("Search API response did not contain a results list named 'data'.")
    return payload


def _json_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def build_csv_rows(
    cases: list[dict[str, Any]],
    responses: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_rows: list[dict[str, Any]] = []
    ranking_rows: list[dict[str, Any]] = []

    for case, response in zip(cases, responses):
        plan = response.get("query_plan") or {}
        data = response.get("data") or []
        summary_rows.append({
            "case_id": case["id"],
            "category": case["category"],
            "scenario": case["scenario"],
            "query": case["query"],
            "total_count": response.get("total_count", len(data)),
            "returned_count": len(data),
            "config_version": response.get("config_version"),
            "index_version": response.get("index_version"),
            "query_type": plan.get("query_type"),
            "lexical_candidates": plan.get("lexical_candidates"),
            "semantic_candidates_kept": plan.get("semantic_candidates_kept"),
            "semantic_candidates_rejected": plan.get("semantic_candidates_rejected"),
            "matched_values": _json_cell(plan.get("matched_values")),
            "unmatched_tokens": _json_cell(plan.get("unmatched_tokens")),
        })

        for rank, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            signals = item.get("ranking_signals") or {}
            ranking_rows.append({
                "case_id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "rank": rank,
                "document_id": item.get("id"),
                "title": item.get("title"),
                "score": item.get("score"),
                "lexical_score": item.get("lexical_score"),
                "semantic_score": item.get("semantic_score"),
                "phrase_score": item.get("phrase_score"),
                "matched_fields": _json_cell(item.get("matched_fields")),
                "lexical_rank": signals.get("lexical_rank"),
                "semantic_rank": signals.get("semantic_rank"),
                "query_term_coverage": signals.get("query_term_coverage"),
                "matched_terms": _json_cell(signals.get("matched_terms")),
                "missing_terms": _json_cell(signals.get("missing_terms")),
                "rank_fusion_score": signals.get("rank_fusion_score"),
                "constraint_boost": signals.get("constraint_boost"),
                "config_version": response.get("config_version"),
                "index_version": response.get("index_version"),
            })
    return summary_rows, ranking_rows


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture People, Jobs and Articles search results for regression comparison."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--base-url",
        default=os.getenv("SEARCH_ADMIN_API_BASE_URL", "http://127.0.0.1:8002"),
        help="Search AI base URL (default: http://127.0.0.1:8002).",
    )
    parser.add_argument("--category", choices=["all", "people", "jobs", "articles"], default="all")
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only this case ID; repeat the option to select several cases.",
    )
    parser.add_argument("--page-size", type=int, default=10, help="Results to capture per query (1-100).")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Validate and list cases without API calls.")
    args = parser.parse_args()

    try:
        cases = load_cases(args.cases)
        if args.category != "all":
            cases = [case for case in cases if case["category"] == args.category]
        if args.case_id:
            wanted = set(args.case_id)
            unknown = wanted - {case["id"] for case in cases}
            if unknown:
                raise ValueError("Unknown case ID(s) for this selection: " + ", ".join(sorted(unknown)))
            cases = [case for case in cases if case["id"] in wanted]
        if not cases:
            raise ValueError("No cases selected.")
        if not 1 <= args.page_size <= 100:
            raise ValueError("--page-size must be between 1 and 100.")
        if args.timeout <= 0:
            raise ValueError("--timeout must be greater than zero.")
        if args.dry_run:
            print(f"Validated {len(cases)} case(s); no API calls were made.")
            for case in cases:
                print(f"- [{case['category']}] {case['id']}: {case['query']}")
            print(f"Planned API requests: {len(cases)} (sequential, one per case, no retries).")
            return 0

        token = os.getenv("SEARCH_ADMIN_API_TOKEN", "").strip()
        if not token:
            raise ValueError(
                "SEARCH_ADMIN_API_TOKEN was not found. Set it in py-backend/.env "
                "or in the current PowerShell session."
            )

        report_cases: list[dict[str, Any]] = []
        successful_responses: list[dict[str, Any]] = []
        successful_cases: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for index, case in enumerate(cases, start=1):
            print(f"[{index}/{len(cases)}] {case['category']} / {case['id']} ...", flush=True)
            try:
                response = call_search(
                    args.base_url,
                    token,
                    case,
                    page_size=args.page_size,
                    timeout=args.timeout,
                )
                result_data = response["data"]
                successful_cases.append(case)
                successful_responses.append(response)
                report_cases.append({
                    "case": case,
                    "status": "ok",
                    "config_version": response.get("config_version"),
                    "index_version": response.get("index_version"),
                    "total_count": response.get("total_count", len(result_data)),
                    "returned_count": len(result_data),
                    "query_plan": response.get("query_plan"),
                    "response": response,
                })
                print(
                    f"    {len(result_data)} captured / "
                    f"{response.get('total_count', len(result_data))} total; "
                    f"config v{response.get('config_version')} / index v{response.get('index_version')}",
                    flush=True,
                )
            except Exception as exc:
                errors.append({"case_id": case["id"], "category": case["category"], "error": str(exc)})
                report_cases.append({"case": case, "status": "error", "error": str(exc)})
                print(f"    ERROR: {exc}", file=sys.stderr, flush=True)

        summary_rows, ranking_rows = build_csv_rows(successful_cases, successful_responses)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / f"search-regression-{timestamp}.json"
        summary_path = args.output_dir / f"search-regression-summary-{timestamp}.csv"
        ranking_path = args.output_dir / f"search-regression-results-{timestamp}.csv"

        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "api_endpoint": args.base_url.rstrip("/") + SEARCH_PATH,
            "page_size": args.page_size,
            "request_count": len(cases),
            "successful_case_count": len(successful_responses),
            "error_count": len(errors),
            "cases": report_cases,
            "errors": errors,
            "notes": [
                "This is a result-capture report, not a relevance-scored benchmark.",
                "Full API responses are retained in the JSON report for later comparison.",
                "CSV files contain query-level metadata and flattened ranking diagnostics.",
                "One sequential request is sent per case; no automatic retries are performed.",
                "The JSON response can contain profile information; keep these local reports out of Git.",
            ],
        }
        json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        summary_fields = [
            "case_id", "category", "scenario", "query", "total_count", "returned_count",
            "config_version", "index_version", "query_type", "lexical_candidates",
            "semantic_candidates_kept", "semantic_candidates_rejected", "matched_values",
            "unmatched_tokens",
        ]
        ranking_fields = [
            "case_id", "category", "query", "rank", "document_id", "title", "score",
            "lexical_score", "semantic_score", "phrase_score", "matched_fields",
            "lexical_rank", "semantic_rank", "query_term_coverage", "matched_terms",
            "missing_terms", "rank_fusion_score", "constraint_boost",
            "config_version", "index_version",
        ]
        write_csv(summary_path, summary_rows, summary_fields)
        write_csv(ranking_path, ranking_rows, ranking_fields)

        print(
            f"Completed {len(successful_responses)}/{len(cases)} searches; "
            f"{len(errors)} error(s)."
        )
        print(f"Full report: {json_path}")
        print(f"Query summary: {summary_path}")
        print(f"Ranked results: {ranking_path}")
        return 1 if errors else 0

    except (OSError, ValueError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

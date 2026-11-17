"""Run a small, controlled relevance benchmark against the local Search AI Admin API.

The script makes one sequential API request per case, with no automatic retries.
It uses only the Python standard library plus the repository's pure metric helper.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.search_ai.services.search_evaluation import evaluate_ranking


DEFAULT_CASES = Path("evaluation/search-cases.json")
SEARCH_PATH = "/api/admin/platform/search"


def load_cases(path: Path) -> tuple[int, list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(
            f"Cases file not found: {path}. Copy "
            "'evaluation/search-cases.template.json' to "
            "'evaluation/search-cases.json' and add reviewed test cases."
        ) from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cases file is not valid JSON: {exc}") from None

    if not isinstance(payload, dict):
        raise ValueError("The cases file must contain a JSON object.")
    k = payload.get("k", 5)
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
        raise ValueError("The file's k value must be an integer from 1 to 100.")

    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("The cases file must contain at least one item in 'cases'.")

    seen_case_ids: set[str] = set()
    validated: list[dict[str, Any]] = []
    for position, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {position} must be a JSON object.")
        case_id = str(case.get("id", "")).strip()
        category = str(case.get("category", "")).strip()
        query = str(case.get("query", "")).strip()
        if not case_id or not category or not query:
            raise ValueError(
                f"Case {position} needs non-empty 'id', 'category' and 'query' values."
            )
        if case_id in seen_case_ids:
            raise ValueError(f"Duplicate case id: {case_id}")
        seen_case_ids.add(case_id)

        filters = case.get("filters", [])
        if not isinstance(filters, list) or any(not isinstance(item, dict) for item in filters):
            raise ValueError(f"Case '{case_id}': filters must be a list of JSON objects.")

        relevant_ids = case.get("relevant_ids", [])
        if not isinstance(relevant_ids, list):
            raise ValueError(f"Case '{case_id}': relevant_ids must be a list.")
        cleaned_relevant = [str(item).strip() for item in relevant_ids if str(item).strip()]
        expected_no_results = case.get("expected_no_results", False)
        if not isinstance(expected_no_results, bool):
            raise ValueError(f"Case '{case_id}': expected_no_results must be true or false.")
        if expected_no_results and cleaned_relevant:
            raise ValueError(
                f"Case '{case_id}': expected_no_results=true cannot have relevant_ids."
            )
        if not expected_no_results and not cleaned_relevant:
            raise ValueError(
                f"Case '{case_id}': add at least one relevant_ids value, "
                "or set expected_no_results=true for a deliberate no-result test."
            )

        validated.append({
            "id": case_id,
            "category": category,
            "query": query,
            "relevant_ids": cleaned_relevant,
            "expected_no_results": expected_no_results,
            "filters": filters,
        })
    return k, validated


def call_search(
    base_url: str,
    token: str,
    case: dict[str, Any],
    *,
    k: int,
    timeout: float = 30.0,
) -> dict[str, Any]:
    body = {
        "query": case["query"],
        "category": case["category"],
        "page": 1,
        "page_size": k,
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
        raise RuntimeError(f"Could not reach the Search AI API: {exc.reason}") from None
    except TimeoutError:
        raise RuntimeError(f"Search request timed out after {timeout:g} seconds.") from None
    except json.JSONDecodeError:
        raise RuntimeError("Search API returned a response that was not valid JSON.") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError("Search API response did not contain a results list named 'data'.")
    return payload


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [
        float(row[key]) for row in rows
        if isinstance(row.get(key), (int, float))
    ]
    return sum(values) / len(values) if values else None


def summarize(rows: list[dict[str, Any]], *, k: int) -> dict[str, Any]:
    metric_rows = [row for row in rows if row.get("metrics") is not None]
    count = len(rows)
    expected_empty = [row for row in rows if row.get("expected_no_results")]
    correct_empty = sum(1 for row in expected_empty if row.get("returned_count", 0) == 0)
    return {
        "case_count": count,
        "scored_case_count": len(metric_rows),
        f"precision@{k}": _mean(metric_rows, "precision_at_k"),
        f"recall@{k}": _mean(metric_rows, "recall_at_k"),
        f"mrr@{k}": _mean(metric_rows, "mrr_at_k"),
        f"ndcg@{k}": _mean(metric_rows, "ndcg_at_k"),
        "zero_result_count": sum(1 for row in rows if row.get("returned_count", 0) == 0),
        "zero_result_rate": (
            sum(1 for row in rows if row.get("returned_count", 0) == 0) / count
            if count else 0.0
        ),
        "expected_no_result_case_count": len(expected_empty),
        "expected_no_result_cases_correct": correct_empty,
    }


def run_benchmark(
    cases: list[dict[str, Any]],
    *,
    base_url: str,
    token: str,
    k: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for case in cases:
        try:
            response = call_search(base_url, token, case, k=k)
            retrieved_ids = [
                str(item["id"]) for item in response["data"]
                if isinstance(item, dict) and item.get("id") is not None
            ]
            returned_count = int(response.get("total_count", len(retrieved_ids)))
            row: dict[str, Any] = {
                "id": case["id"],
                "category": case["category"],
                "query": case["query"],
                "expected_no_results": case["expected_no_results"],
                "relevant_ids": case["relevant_ids"],
                "retrieved_ids": retrieved_ids,
                "returned_count": returned_count,
                "index_version": response.get("index_version"),
                "config_version": response.get("config_version"),
            }
            if case["expected_no_results"]:
                row["unexpected_results"] = returned_count > 0
                row["metrics"] = None
            else:
                row["metrics"] = evaluate_ranking(
                    retrieved_ids, case["relevant_ids"], k=k
                )
                row["unexpected_results"] = False
            results.append(row)
        except Exception as exc:
            errors.append({
                "case_id": case["id"],
                "category": case["category"],
                "error": str(exc),
            })

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_category[row["category"]].append(row)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "api_endpoint": base_url.rstrip("/") + SEARCH_PATH,
        "k": k,
        "request_count": len(cases),
        "successful_case_count": len(results),
        "error_count": len(errors),
        "overall": summarize(results, k=k),
        "by_category": {
            category: summarize(category_rows, k=k)
            for category, category_rows in sorted(by_category.items())
        },
        "cases": results,
        "errors": errors,
        "notes": [
            "Metrics use binary relevance labels supplied in relevant_ids.",
            "Document IDs are compared exactly and case-sensitively.",
            "Precision@k uses k as its denominator even when fewer than k results are returned.",
            "Expected-no-result cases are checked separately and are not included in ranking metric averages.",
            "This is an evaluation report, not an automatic search-quality pass/fail decision.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate People, Jobs and Articles search against manually judged relevant document IDs."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="JSON case file (default: evaluation/search-cases.json)")
    parser.add_argument("--base-url", default=os.getenv("SEARCH_ADMIN_API_BASE_URL", ""), help="Local Search AI base URL; or set SEARCH_ADMIN_API_BASE_URL")
    parser.add_argument("--k", type=int, default=None, help="Override the file's top-k value (1-100)")
    parser.add_argument("--max-cases", type=int, default=None, help="Run at most this many cases; useful for a small first pass")
    parser.add_argument("--timeout", type=float, default=30.0, help="Seconds to wait for each request (default: 30)")
    parser.add_argument("--output", type=Path, default=None, help="Optional path for the detailed JSON report")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list cases without sending API requests")
    args = parser.parse_args()

    try:
        file_k, cases = load_cases(args.cases)
        k = args.k if args.k is not None else file_k
        if isinstance(k, bool) or not 1 <= k <= 100:
            raise ValueError("k must be between 1 and 100.")
        if args.max_cases is not None:
            if args.max_cases < 1:
                raise ValueError("--max-cases must be at least 1.")
            cases = cases[:args.max_cases]
        if args.timeout <= 0:
            raise ValueError("--timeout must be greater than zero.")

        if args.dry_run:
            print(f"Validated {len(cases)} case(s); no API calls were made.")
            for case in cases:
                print(f"- [{case['category']}] {case['id']}: {case['query']}")
            print(f"Planned API requests: {len(cases)} (one per case, sequential, no retries).")
            return 0

        token = os.getenv("SEARCH_ADMIN_API_TOKEN", "").strip()
        if not args.base_url.strip():
            raise ValueError("Set SEARCH_ADMIN_API_BASE_URL or pass --base-url.")
        if not token:
            raise ValueError("Set SEARCH_ADMIN_API_TOKEN in the environment; do not put it in the case file.")

        report = run_benchmark(
            cases,
            base_url=args.base_url.strip(),
            token=token,
            k=k,
        )
        # Apply the caller-selected timeout at the request layer via the CLI default.
        # The default 30-second timeout is intentionally conservative for local model loading.
        print(f"Completed {report['successful_case_count']}/{report['request_count']} case(s); {report['error_count']} error(s).")
        summary = report["overall"]
        print(
            f"Overall: Precision@{k}={summary[f'precision@{k}']}, "
            f"Recall@{k}={summary[f'recall@{k}']}, "
            f"MRR@{k}={summary[f'mrr@{k}']}, "
            f"NDCG@{k}={summary[f'ndcg@{k}']}, "
            f"zero-result rate={summary['zero_result_rate']:.1%}"
        )
        for category, values in report["by_category"].items():
            print(
                f"{category}: {values['case_count']} case(s), "
                f"Precision@{k}={values[f'precision@{k}']}, "
                f"Recall@{k}={values[f'recall@{k}']}, "
                f"MRR@{k}={values[f'mrr@{k}']}, "
                f"NDCG@{k}={values[f'ndcg@{k}']}"
            )
        if report["errors"]:
            for error in report["errors"]:
                print(f"ERROR [{error['category']}/{error['case_id']}]: {error['error']}", file=sys.stderr)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(f"Detailed report saved to: {args.output}")
        return 1 if report["error_count"] else 0
    except (OSError, ValueError) as exc:
        print(f"Evaluation setup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

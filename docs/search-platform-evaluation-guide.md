# Search Platform Evaluation Guide

This guide explains how to prepare one small, repeatable set of real search examples and run it against People, Jobs and Articles.

The purpose is to answer a practical question: **are the right records appearing near the top for the queries people actually use?**

The evaluation toolkit does not change search settings, source data or indexed records. It sends one read-only Admin search request per case, sequentially, and records the results for comparison.

## 1. What you need before running it

- The local Search AI backend must be running.
- The category must exist, be active, and have indexed records.
- The Admin API token must be available in your local environment.
- A person who understands the data must review which record IDs are relevant for each test query.

The tool calls `POST /api/admin/platform/search`. It does not call the public search endpoint and does not alter category configuration or documents. It does not automatically retry failed requests.

## 2. Create your local test-case file

From the `py-backend` folder in PowerShell:

```powershell
Copy-Item evaluation\search-cases.template.json evaluation\search-cases.json
notepad evaluation\search-cases.json
```

The template is intentionally empty. Add reviewed test cases using the following structure:

```json
{
  "k": 5,
  "cases": [
    {
      "id": "people-001",
      "category": "people",
      "query": "Python developers in Indore",
      "relevant_ids": ["<real-person-document-id>"],
      "filters": []
    },
    {
      "id": "jobs-001",
      "category": "jobs",
      "query": "Remote backend jobs requiring Python",
      "relevant_ids": ["<real-job-document-id-1>", "<real-job-document-id-2>"],
      "filters": []
    },
    {
      "id": "articles-001",
      "category": "articles",
      "query": "Guides about semantic search",
      "relevant_ids": ["<real-article-document-id>"],
      "filters": []
    },
    {
      "id": "jobs-002",
      "category": "jobs",
      "query": "A job title that is not present in this test dataset",
      "relevant_ids": [],
      "expected_no_results": true,
      "filters": []
    }
  ]
}
```

The angle-bracket values above are examples only. Replace them with actual IDs from the local category data before running the evaluation. Do not leave placeholder IDs in a real benchmark.

### What each case field means

| Property | Required? | Meaning |
|---|---|---|
| `id` | Yes | A unique name for this test case, such as `people-001`. |
| `category` | Yes | Category ID, such as `people`, `jobs` or `articles`. |
| `query` | Yes | The exact words a user would type. |
| `relevant_ids` | Yes, unless it is an expected-empty case | All document IDs that reviewers have judged relevant to this query. Use real IDs and preserve their exact spelling/case. |
| `expected_no_results` | No | Set to `true` only when the correct outcome is no results. |
| `filters` | No | Optional explicit search-result filters, using the normal API filter format. Use an empty list when no explicit filters are being tested. |

For an explicit filter test, the format is, for example:

```json
"filters": [
  {"field": "city", "operator": "eq", "value": "Indore"}
]
```

A numeric range example is:

```json
"filters": [
  {"field": "salary", "operator": "between", "values": [50000, 100000]}
]
```

The field names and operators must match the actual category configuration. This test file is local and is ignored by Git because query text and document IDs may be sensitive.

### How to choose relevant IDs

A reviewer should search or inspect the category data and decide which records genuinely answer the query.

- Include every known relevant record in `relevant_ids`, not just the one you hope will rank first.
- Do not label a record relevant just because it appeared in the search results.
- Keep the same relevance judgement when comparing two search versions.
- Use a mixture of common, exact, ambiguous, spelling-variant, multi-field and filter-based queries.
- Add a few intentional no-result cases to check that unrelated queries do not return misleading matches.

The first version uses **binary relevance**: a record is either relevant or not relevant. Graded relevance (for example, highly relevant versus partially relevant) can be added later if reviewers need it.

## 3. Recommended coverage

Start with a manageable set. Around 10–20 reviewed queries per category is a useful first benchmark, but quality of judgement matters more than the number of queries.

| Query group | People example | Jobs example | Articles example |
|---|---|---|---|
| Exact lookup | A person's exact name or ID | Exact job title or ID | Exact article title |
| Field/value | Python expertise | Remote work mode | AI/ML category |
| Multiple conditions | Python and Indore | Python, Indore and full-time | Topic and publication category |
| Natural language | “Backend developer with API experience” | “Entry-level backend role” | “How to build a semantic search system” |
| Synonyms | Developer / programmer | Permanent / full-time | Guide / tutorial |
| Typo or variant | A common misspelling | A common job-title variant | An alternate spelling of a topic |
| Explicit filter | City equals Indore | Salary between two values | Published date after a date |
| No-result check | A skill/location combination absent from the data | A role that is not in the dataset | A topic not covered by the collection |

Do not invent expected record IDs. Obtain them from the actual local dataset and have a reviewer confirm them.

## 4. Run a safe dry-run first

Activate the existing virtual environment, then make sure the current directory is `py-backend`:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\evaluate_platform_search.py --cases evaluation\search-cases.json --dry-run
```

A dry-run validates the file and lists the cases. **It sends zero API requests.**

If the test list looks correct, set the local API URL and Admin token in the current PowerShell window:

```powershell
$env:SEARCH_ADMIN_API_BASE_URL = "http://127.0.0.1:8002"
$env:SEARCH_ADMIN_API_TOKEN = "<your-local-admin-token>"
```

Use the port on which your local Search AI service is actually running. Keep the token in the environment; do not place it in the JSON file, source code or Git.

Run a small smoke test first:

```powershell
python scripts\evaluate_platform_search.py --cases evaluation\search-cases.json --max-cases 3 --output evaluation\reports\smoke-report.json
```

Then run the full reviewed set:

```powershell
python scripts\evaluate_platform_search.py --cases evaluation\search-cases.json --output evaluation\reports\search-report.json
```

The script sends **one sequential request per case**, with no automatic retries. Use `--max-cases` for a deliberately small first run. The default top-`k` is 5; it can be changed in the JSON file or with `--k 10`.

## 5. What the measurements mean

Each ranking metric is between 0 and 1. For these metrics, **higher is better**.

| Metric | Plain-language meaning | How to read it |
|---|---|---|
| **Precision@k** | Of the first `k` result positions, how many contain relevant records? | Higher means fewer irrelevant records are taking up the top positions. |
| **Recall@k** | Of all records marked relevant for this query, how many were found in the first `k`? | Higher means fewer known relevant records are being missed near the top. |
| **MRR@k** | How high is the first relevant record? | 1.0 means the first result is relevant; 0.5 means the first relevant result is second; 0 means none was found in the first `k`. |
| **NDCG@k** | How good is the ordering of relevant records? | Gives more credit when relevant records appear earlier. In this first version relevance is binary. |
| **Zero-result rate** | How often did the API return no results? | This is a diagnostic, not a target to minimize blindly. Some test queries are intentionally expected to return nothing. |
| **Unexpected zero-result rate** | How often did a query with known relevant records return nothing? | Lower is better; a high value suggests missed results or over-restrictive filtering. |
| **Expected no-result accuracy** | How often did intentional no-result queries correctly return no records? | Higher is better; unexpected matches may indicate overly broad retrieval. |

The report gives an overall average and a separate average for each category. It also records the IDs returned, the corrected query, query-plan diagnostics, and the configuration/index versions used for each case.

**Do not use one metric by itself.** For example, a system that returns only one record could have good precision but poor recall. Read Precision@k and Recall@k together.

The toolkit reports measurements; it does not decide that a category passes. The team should agree on acceptable targets after seeing the first baseline.

## 6. Compare two search versions fairly

To compare the previous and updated search versions:

1. Keep the exact same case file and relevance judgements.
2. Run the cases against the previous version, if that environment is still available, and save its report under a distinct name.
3. Run the same cases against the updated local version and save a second report.
4. Compare metrics by category and inspect the cases where results changed.
5. Check configuration and index versions in each report. Do not compare runs if the underlying category data or relevance labels changed without recording that fact.

Example report names:

- `evaluation/reports/baseline-before-update.json`
- `evaluation/reports/updated-local.json`

A better average score does not excuse a serious regression on an important query. Inspect individual cases as well as the averages.

## 7. Safety and limitations

- The evaluation uses the local Search AI Admin API and its configured search stack. It does not call Groq or an external LLM for evaluation.
- The first query can take longer if the local embedding model/index has not been warmed up.
- Requests run sequentially. There is no concurrency, retry loop or repeated test cycle.
- The toolkit does not modify category configuration, source records, indexed documents or relevance feedback.
- The first version uses binary relevance and exact document-ID comparison. It does not perform automatic model training.
- Test queries and returned document IDs can be sensitive. Keep `search-cases.json` and generated reports local; the evaluation folder's `.gitignore` excludes them from Git.
- This is an initial QA tool, not a production monitoring or load-testing system.

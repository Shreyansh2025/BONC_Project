# Search configuration baseline review

Date: 2026-11-19

Working branch: \`feature/search-ranking-v2\`

Preserved baseline commit: \`bd17553305b9ce862107fb2f17e1c44ca79cfe75\`

Development branch: \`feature/search-config-and-test-tooling\`

## Preservation

- Remote backup branch: \`backup/search-ranking-v2-baseline-2026-11-19\`
- The backup branch was compared with \`feature/search-ranking-v2\`; GitHub reported them as identical (0 commits ahead/behind).
- This is a remote Git checkpoint. It does not capture any uncommitted files or local-only changes on a developer workstation.
- Local SQL snapshot script: \`evaluation/snapshot_search_baseline.sql\`.
- Search-result capture uses \`scripts/capture_platform_search.py\`; result files are local-only and ignored by Git.

## What configuration values actually affect

Code inspection on \`feature/search-config-and-test-tooling\` confirms:

1. \`SearchFieldConfig.weight\` is used in BM25F field scoring. The effective lexical field boost is configured weight multiplied by a fixed role multiplier.
2. The current BM25F role multipliers are: title 2.6, skill 2.2, category 1.45, location 1.25, description 1.0 and general 1.0.
3. Configured field weights are also used to combine the per-field document embeddings for semantic retrieval. This path uses the configured numeric field weights; the BM25F role multipliers are not applied to those embedding weights.
4. \`lexical_weight\` and \`semantic_weight\` are consumed by reciprocal-rank fusion when both lexical and semantic candidate lists exist.
5. \`semantic_min_score\` is the minimum embedding similarity score accepted as a semantic candidate.
6. \`semantic_high_confidence_score\` is used by the semantic candidate acceptance gate. With insufficient literal overlap, the gate requires a stronger score: at least max(90, configured high-confidence score + 10), capped at 100.
7. Recognised fields are ranking hints by default. A query constraint becomes a required automatic filter only when a field is explicitly configured as filterable and \`query_filter_mode="required"\`. This is not currently enabled in the three supplied category configurations.
8. The preferred-constraint bonus has a separate, fixed role-boost table. It is distinct from BM25F and from semantic embedding weights.

## Initial category observations (no settings changed)

### People

Current configured weights and roles:
- \`full_name\`: title, 5.0
- \`location\`: location, 4.0
- \`profession\`: category, 3.0
- \`profile_text\`: description, 2.0

For BM25F, the nominal configured-weight × role-multiplier values are:
- full_name: 13.0
- location: 5.0
- profession: 4.35
- profile_text: 2.0

These are pre-normalization field boosts, not a final score. The current setup makes location's BM25F field boost slightly larger than profession's. The search examples show why profession and core technical skills deserve further review. Consider adding a distinct, curated skills field only if the source data supports reliable extraction.

### Jobs

Current fields place title and key skills at weight 5.0, requirements at 4.0, description and responsibilities at 3.0, and benefits at 1.0. The BM25F role multipliers make title and skills strong lexical signals.

The main observed weakness is that recognised skill/role values are currently ranking hints, not mandatory constraints. A profile can therefore match generic backend/developer language while missing Python. Avoid turning every skill into a required filter: multi-skill AND rules can incorrectly return no results. Evaluate optional/preferred versus explicitly required skills separately.

The current configuration description says "Search BONC people profiles"; this is a descriptive metadata error worth correcting in a reviewed configuration update.

### Articles

Current fields:
- title: title role, 5.0
- body: description role, 3.0
- hashtags: category role, 3.0
- article_type: category role, 2.0
- author: general role, 1.5

This is a reasonable baseline and the six manual searches returned strong first matches. Preserve these settings initially. Review only specific broad-query false positives after a controlled comparison.

## Existing source-mapping automation

The current Search AI admin API already provides:
- Source profile discovery without exposing connection strings.
- SQL schema/table/column discovery.
- Primary-key and foreign-key inspection.
- Column-name-based field-role suggestions.
- Default weight, alias, data-type and filterability suggestions.
- A safe SQL join-view preview for supported many-to-one joins.

Important limits:
- Mapping results are suggestions for an administrator to review; they are not silently applied.
- Status/deletion filters are not inferred automatically because valid values depend on the source.
- The join-view feature generates a preview script; it does not execute the database DDL.
- It maps existing source columns. It does not extract or infer new skill/topic values from free-text fields.

## Safe field-automation candidates to evaluate later

1. Normalize whitespace, casing and common delimiter formats in derived/index fields while retaining original source values.
2. Suggest skills from structured skill columns first. Consider extracting skill phrases from profile/job text only as a separate derived field with provenance and review.
3. Suggest a profession/job-role label from an explicit title or profession column; do not infer a new role from weak text evidence.
4. Extract article topic phrases from title and hashtags as suggestions; do not replace editor-supplied tags.
5. Flag missing primary fields, empty profile text, inconsistent category labels and duplicated values during indexing.
6. Never automatically create required filters or overwrite source records based on inferred content.

## Next validation sequence

1. Confirm the local working tree is clean or separately preserve local uncommitted changes.
2. Run the SQL snapshot script against the local Search AI database and verify all six copied row counts.
3. Run the 18-case capture script against the current local API. Keep its JSON/CSV output local.
4. Review source-view field completeness and actual populated values for People, Jobs and Articles.
5. Design simpler administrator-facing controls and category-specific configuration templates.
6. Change configuration first, rerun the same cases, then decide whether ranking code needs modification.

No category configuration, source data, or ranking code has been modified in this checkpoint work.

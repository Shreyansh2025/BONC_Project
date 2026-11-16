export type SearchKind = "all" | "business" | "product";

export interface SearchResult {
  type?: "business" | "product" | string;
  name?: string;
  businessName?: string;
  productName?: string;
  companyName?: string;
  tagline?: string;
  description?: string;
  address1?: string;
  city?: string;
  state?: string;
  country?: string;
  pincode?: string;
  categoryName?: string;
  category?: string;
  brandName?: string;
  model?: string;
  minPrice?: string;
  maxPrice?: string;
  slug?: string;
  matchType?: string;
  matchPercentage?: number;
  matchedKeyword?: string;
  id?: string | number;
  _id?: string | number;
  [key: string]: unknown;
}

export interface SearchResponse {
  Success: boolean;
  Message: string;
  Type: string;
  Data: SearchResult[];
  TotalCount: number;
}

export interface IndexSnapshot {
  count?: number;
  index_built?: boolean;
  status?: string;
  last_error?: string;
  [key: string]: unknown;
}

export interface ReadinessResponse {
  status: string;
  service?: string;
  checks?: {
    database?: string;
    embeddingModel?: string;
    indexes?: Record<string, IndexSnapshot>;
  };
  initializationError?: string | null;
}

export interface SearchRulesSettings {
  semantic_candidate_limit: number;
  lexical_result_threshold: number;
  company: {
    semantic_min_score: number;
    semantic_high_confidence_score: number;
  };
  b2b_product: {
    semantic_min_score: number;
    semantic_high_confidence_score: number;
  };
  local_product: {
    semantic_min_score: number;
  };
}

export interface SearchSettingsRecord {
  version: number;
  settings: SearchRulesSettings;
  updated_at_utc?: string | null;
  updated_by?: string | null;
}

export interface SearchSettingsHistoryItem {
  history_id: number;
  version: number;
  settings: SearchRulesSettings;
  changed_at_utc?: string | null;
  changed_by?: string | null;
  change_reason?: string | null;
}

const rawBase = import.meta.env.VITE_SEARCH_API_BASE_URL || "/search-api";
export const SEARCH_API_BASE = rawBase.replace(/\/$/, "");

async function readJson<T>(response: Response, allowStatuses: number[] = []): Promise<T> {
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Search AI returned an unreadable response (HTTP ${response.status}).`);
  }

  if (!response.ok && !allowStatuses.includes(response.status)) {
    const message =
      payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
        ? payload.detail
        : payload && typeof payload === "object" && "message" in payload && typeof payload.message === "string"
          ? payload.message
          : `Request failed with HTTP ${response.status}.`;
    throw new Error(message);
  }
  return payload as T;
}

export async function runSearch(input: {
  query: string;
  page: number;
  pageSize: number;
  kind: SearchKind;
}): Promise<SearchResponse> {
  const response = await fetch(`${SEARCH_API_BASE}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      SearchText: input.query,
      PageNo: input.page,
      PageSize: input.pageSize,
      Type: input.kind,
    }),
  });
  const result = await readJson<SearchResponse>(response);
  if (!result.Success) throw new Error(result.Message || "Search request failed.");
  return result;
}

export async function getReadiness(): Promise<ReadinessResponse> {
  const response = await fetch(`${SEARCH_API_BASE}/health/ready`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return readJson<ReadinessResponse>(response, [503]);
}

export async function getSearchSettings(): Promise<SearchSettingsRecord> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/search-settings`, {
    headers: { Accept: "application/json" },
  });
  return readJson<SearchSettingsRecord>(response);
}

export async function updateSearchSettings(input: {
  expectedVersion: number;
  settings: SearchRulesSettings;
  changeReason: string;
}): Promise<SearchSettingsRecord> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/search-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      expected_version: input.expectedVersion,
      settings: input.settings,
      change_reason: input.changeReason.trim() || null,
    }),
  });
  return readJson<SearchSettingsRecord>(response);
}

export async function getSearchSettingsHistory(limit = 50): Promise<SearchSettingsHistoryItem[]> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/search-settings/history?limit=${limit}`, {
    headers: { Accept: "application/json" },
  });
  const payload = await readJson<{ items: SearchSettingsHistoryItem[] }>(response);
  return payload.items || [];
}


export type PlatformFieldRole = "title" | "skill" | "location" | "category" | "description" | "general";

export interface PlatformFieldConfig {
  key: string;
  source_column: string;
  label: string;
  role: PlatformFieldRole;
  weight: number;
  searchable: boolean;
  semantic: boolean;
  aliases: string[];
}

export interface PlatformRulesConfig {
  lexical_enabled: boolean;
  semantic_enabled: boolean;
  semantic_min_score: number;
  semantic_candidate_limit: number;
  lexical_weight: number;
  semantic_weight: number;
}

export interface PlatformSourceFilter {
  column: string;
  operator: "eq" | "ne" | "in" | "is_null" | "not_null";
  value?: string | number | boolean | null;
  values?: Array<string | number | boolean>;
}

export interface PlatformCategoryConfig {
  category_id: string;
  display_name: string;
  description?: string | null;
  source:
    | { type: "push" }
    | {
        type: "sqlserver";
        connection_env: string;
        schema: string;
        table: string;
        id_column: string;
        max_records: number;
        filters: PlatformSourceFilter[];
      };
  fields: PlatformFieldConfig[];
  rules: PlatformRulesConfig;
  active: boolean;
  public_search_enabled: boolean;
}

export interface PlatformSourceProfile {
  profile_id: string;
  connection_env: string;
}

export interface PlatformSourceColumn {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key?: boolean;
  is_foreign_key?: boolean;
}

export interface PlatformSourceTable {
  schema: string;
  name: string;
  type: "BASE TABLE" | "VIEW" | string;
  primary_key_columns?: string[];
  columns: PlatformSourceColumn[];
}

export interface PlatformSourceRelationship {
  constraint_name: string;
  from_schema: string;
  from_table: string;
  to_schema: string;
  to_table: string;
  columns: Array<{
    from_column: string;
    to_column: string;
    ordinal_position: number;
  }>;
}

export interface PlatformSourceCatalog {
  profile_id: string;
  database: string;
  schemas: string[];
  tables: PlatformSourceTable[];
  relationships?: PlatformSourceRelationship[];
}

export interface PlatformMappingSuggestionField extends PlatformFieldConfig {
  confidence: "high" | "medium" | "low" | string;
  reason: string;
}

export interface PlatformSourceMappingSuggestions {
  profile_id: string;
  database: string;
  schema: string;
  table: string;
  table_type: string;
  id_column_suggestion: {
    column: string | null;
    confidence: "high" | "medium" | "none" | string;
    reason: string;
  };
  fields: PlatformMappingSuggestionField[];
  skipped_columns: Array<{ column: string; reason: string }>;
  potential_filter_columns: string[];
  warnings: string[];
}

export interface PlatformJoinViewPreview {
  profile_id: string;
  database: string;
  view_schema: string;
  view_name: string;
  document_id_column: string;
  joined_tables: Array<{ schema: string; table: string }>;
  relationship_count: number;
  view_sql: string;
  execution_performed: false;
  warnings: string[];
}

export async function getPlatformSourceProfiles(): Promise<PlatformSourceProfile[]> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/sources`, {
    headers: { Accept: "application/json" },
  });
  const payload = await readJson<{ items: PlatformSourceProfile[] }>(response);
  return payload.items || [];
}

export async function discoverPlatformSourceSchema(input: {
  profileId: string;
  schema?: string;
}): Promise<PlatformSourceCatalog> {
  const params = new URLSearchParams();
  if (input.schema) params.set("schema", input.schema);
  const queryString = params.toString();
  const query = queryString ? `?${queryString}` : "";
  const response = await fetch(
    `${SEARCH_API_BASE}/api/admin/platform/sources/${encodeURIComponent(input.profileId)}/schema${query}`,
    { headers: { Accept: "application/json" } },
  );
  return readJson<PlatformSourceCatalog>(response);
}

export async function suggestPlatformSourceMapping(input: {
  profileId: string;
  schema: string;
  table: string;
}): Promise<PlatformSourceMappingSuggestions> {
  const params = new URLSearchParams({
    schema: input.schema,
    table: input.table,
  });
  const response = await fetch(
    `${SEARCH_API_BASE}/api/admin/platform/sources/${encodeURIComponent(input.profileId)}/mapping-suggestions?${params.toString()}`,
    { headers: { Accept: "application/json" } },
  );
  return readJson<PlatformSourceMappingSuggestions>(response);
}

export async function previewPlatformJoinView(input: {
  profileId: string;
  baseSchema: string;
  baseTable: string;
  baseIdColumn: string;
  viewSchema: string;
  viewName: string;
  selectedRelationships: PlatformSourceRelationship[];
}): Promise<PlatformJoinViewPreview> {
  const response = await fetch(
    `${SEARCH_API_BASE}/api/admin/platform/sources/${encodeURIComponent(input.profileId)}/join-view-preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        base_schema: input.baseSchema,
        base_table: input.baseTable,
        base_id_column: input.baseIdColumn,
        view_schema: input.viewSchema,
        view_name: input.viewName,
        selected_relationships: input.selectedRelationships.map((relationship) => ({
          constraint_name: relationship.constraint_name,
          from_schema: relationship.from_schema,
          from_table: relationship.from_table,
          to_schema: relationship.to_schema,
          to_table: relationship.to_table,
        })),
      }),
    },
  );
  return readJson<PlatformJoinViewPreview>(response);
}

export interface PlatformCategoryRecord {
  config: PlatformCategoryConfig;
  status: "active" | "disabled" | string;
  config_version: number;
  index_version: number;
  updated_at_utc?: string | null;
  updated_by?: string | null;
}

export interface PlatformCategoryHistoryItem {
  history_id: number;
  config_version: number;
  config: PlatformCategoryConfig;
  status: string;
  changed_at_utc?: string | null;
  changed_by?: string | null;
  change_reason?: string | null;
}

export interface PlatformSearchResult {
  id: string;
  title: string;
  fields: Record<string, string | number | boolean | null>;
  metadata: Record<string, string | number | boolean | null>;
  score: number;
  lexical_score: number;
  semantic_score: number;
  matched_fields: string[];
  category: string;
}

export interface PlatformQueryConstraint {
  field: string;
  role: PlatformFieldRole;
  values: string[];
  match: "exact_index_value";
  applied_as: "ranking_hint_only";
}

export interface PlatformQueryPlan {
  query_type: "field_guided" | "free_text";
  matched_aliases: Record<string, string[]>;
  matched_values: Record<string, string[]>;
  constraints: PlatformQueryConstraint[];
  unmatched_tokens: string[];
  lexical_query: string;
  semantic_query: string;
  filters_applied: boolean;
  explanation: string;
}

export interface PlatformSearchResponse {
  query: string;
  category: string;
  query_understanding: {
    normalised_query: string;
    query_type: "field_guided" | "free_text";
    field_hints: Record<string, string[]>;
    matched_aliases: Record<string, string[]>;
    matched_values: Record<string, string[]>;
    matched_fields: Record<string, string[]>;
    constraints: PlatformQueryConstraint[];
    lexical_query: string;
    unmatched_tokens: string[];
    explanation: string;
  };
  query_plan: PlatformQueryPlan;
  data: PlatformSearchResult[];
  total_count: number;
  page: number;
  page_size: number;
  config_version: number;
  index_version: number;
}

export async function getPlatformCategories(): Promise<PlatformCategoryRecord[]> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories`, {
    headers: { Accept: "application/json" },
  });
  return readJson<PlatformCategoryRecord[]>(response);
}

export async function createPlatformCategory(
  config: PlatformCategoryConfig,
): Promise<PlatformCategoryRecord> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(config),
  });
  return readJson<PlatformCategoryRecord>(response);
}

export async function syncPlatformCategory(categoryId: string): Promise<{ status: string; category_id: string; documents_synced: number }> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories/${encodeURIComponent(categoryId)}/sync`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  return readJson(response);
}

export async function ingestPlatformDocuments(
  categoryId: string,
  documents: Array<{
    document_id: string;
    title?: string;
    fields: Record<string, string | number | boolean | null>;
    metadata?: Record<string, string | number | boolean | null>;
  }>,
  replaceAll = false,
): Promise<{ status: string; category_id: string; documents_ingested: number }> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories/${encodeURIComponent(categoryId)}/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ documents, replace_all: replaceAll }),
  });
  return readJson(response);
}

export async function runPlatformAdminSearch(input: {
  query: string;
  category: string;
  page?: number;
  pageSize?: number;
}): Promise<PlatformSearchResponse> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      query: input.query,
      category: input.category,
      page: input.page ?? 1,
      page_size: input.pageSize ?? 10,
    }),
  });
  return readJson<PlatformSearchResponse>(response);
}


export async function updatePlatformCategory(input: {
  categoryId: string;
  expectedVersion: number;
  config: PlatformCategoryConfig;
  changeReason?: string;
}): Promise<PlatformCategoryRecord> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories/${encodeURIComponent(input.categoryId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      expected_version: input.expectedVersion,
      config: input.config,
      change_reason: input.changeReason?.trim() || null,
    }),
  });
  return readJson<PlatformCategoryRecord>(response);
}


export async function getPlatformCategoryHistory(categoryId: string): Promise<PlatformCategoryHistoryItem[]> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories/${encodeURIComponent(categoryId)}/history`, {
    headers: { Accept: "application/json" },
  });
  const payload = await readJson<{ items: PlatformCategoryHistoryItem[] }>(response);
  return payload.items || [];
}


export interface PlatformFeedbackItem {
  feedback_id: number;
  query: string;
  document_id: string;
  is_relevant: boolean;
  comment?: string | null;
  created_at_utc?: string | null;
  created_by?: string | null;
  result_rank?: number | null;
  result_score?: number | null;
  config_version?: number | null;
  index_version?: number | null;
}

export interface PlatformFeedbackSummary {
  total_count: number;
  relevant_count: number;
  not_relevant_count: number;
  items: PlatformFeedbackItem[];
}

export async function recordPlatformFeedback(input: {
  categoryId: string;
  query: string;
  documentId: string;
  isRelevant: boolean;
  comment?: string;
  resultRank?: number;
  resultScore?: number;
  configVersion?: number;
  indexVersion?: number;
}): Promise<{ feedback_id: number; category_id: string; document_id: string; is_relevant: boolean }> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories/${encodeURIComponent(input.categoryId)}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      query: input.query,
      document_id: input.documentId,
      is_relevant: input.isRelevant,
      comment: input.comment?.trim() || null,
      result_rank: input.resultRank ?? null,
      result_score: input.resultScore ?? null,
      config_version: input.configVersion ?? null,
      index_version: input.indexVersion ?? null,
    }),
  });
  return readJson(response);
}

export async function getPlatformFeedback(categoryId: string, limit = 100): Promise<PlatformFeedbackSummary> {
  const response = await fetch(`${SEARCH_API_BASE}/api/admin/platform/categories/${encodeURIComponent(categoryId)}/feedback?limit=${limit}`, {
    headers: { Accept: "application/json" },
  });
  return readJson<PlatformFeedbackSummary>(response);
}

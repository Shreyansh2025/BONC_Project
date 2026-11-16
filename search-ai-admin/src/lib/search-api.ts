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
        filters: Array<{
          column: string;
          operator: "eq" | "ne" | "in" | "is_null" | "not_null";
          value?: string | number | boolean | null;
          values?: Array<string | number | boolean>;
        }>;
      };
  fields: PlatformFieldConfig[];
  rules: PlatformRulesConfig;
  active: boolean;
  public_search_enabled: boolean;
}

export interface PlatformCategoryRecord {
  config: PlatformCategoryConfig;
  status: "active" | "disabled" | string;
  config_version: number;
  index_version: number;
  updated_at_utc?: string | null;
  updated_by?: string | null;
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

export interface PlatformSearchResponse {
  query: string;
  category: string;
  query_understanding: {
    normalised_query: string;
    matched_fields: Record<string, string[]>;
    unmatched_tokens: string[];
  };
  data: PlatformSearchResult[];
  total_count: number;
  page: number;
  page_size: number;
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

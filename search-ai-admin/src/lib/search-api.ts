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

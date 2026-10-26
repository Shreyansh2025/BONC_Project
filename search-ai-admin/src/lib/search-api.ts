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

const rawBase = import.meta.env.VITE_SEARCH_API_BASE_URL || "/search-api";
export const SEARCH_API_BASE = rawBase.replace(/\/$/, "");

async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  let payload: unknown;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    throw new Error(`Search AI returned an unreadable response (HTTP ${response.status}).`);
  }

  if (!response.ok && response.status !== 503) {
    const message =
      payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
        ? payload.detail
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
  return readJson<ReadinessResponse>(response);
}

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  getReadiness,
  runSearch,
  SEARCH_API_BASE,
  type ReadinessResponse,
  type SearchKind,
  type SearchResponse,
  type SearchResult,
} from "./lib/search-api";

const PAGE_SIZE_OPTIONS = [10, 25, 50];
const EXAMPLE_QUERIES = [
  "diesel generator",
  "industrial equipment supplier",
  "generator in Indore",
  "1250kW generator",
];

function textValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number") return String(value);
  return "";
}

function cleanText(value: unknown): string {
  return textValue(value)
    .replace(/<[^>]*>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ")
    .trim();
}

function resultTitle(item: SearchResult): string {
  const businessName = cleanText(item.businessName);
  return (
    cleanText(item.name) ||
    (businessName && businessName !== "1" ? businessName : "") ||
    cleanText(item.productName) ||
    cleanText(item.companyName) ||
    (item.type === "business" ? "Unknown business" : "Unnamed result")
  );
}

function resultCategory(item: SearchResult): string {
  return cleanText(item.categoryName) || cleanText(item.category) || cleanText(item.brandName) || "";
}

function resultLocation(item: SearchResult): string {
  return [item.city, item.state, item.country].map(cleanText).filter(Boolean).join(", ");
}

function resultDescription(item: SearchResult): string {
  return cleanText(item.description) || cleanText(item.tagline);
}

function scoreLabel(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${Math.round(value)} pts`;
}

function kindLabel(value: unknown): string {
  return value === "business" ? "Business" : value === "product" ? "Product" : "Result";
}

function indexLabel(key: string): string {
  const names: Record<string, string> = {
    b2bCompanies: "B2B companies",
    b2bProducts: "B2B products",
    localProducts: "BrochureIQ products",
  };
  return names[key] || key;
}

function readinessLabel(readiness: ReadinessResponse | null, loading: boolean, error: string): string {
  if (loading) return "Checking service";
  if (error) return "Unavailable";
  if (readiness?.status === "ready") return "Ready";
  if (readiness?.status === "not_ready") return "Initializing";
  return readiness?.status || "Unknown";
}

export default function App() {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<SearchKind>("all");
  const [pageSize, setPageSize] = useState(10);
  const [page, setPage] = useState(1);
  const [searchData, setSearchData] = useState<SearchResponse | null>(null);
  const [searchedQuery, setSearchedQuery] = useState("");
  const [durationMs, setDurationMs] = useState<number | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [showRaw, setShowRaw] = useState(false);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [readinessError, setReadinessError] = useState("");
  const [history, setHistory] = useState<string[]>([]);

  const refreshReadiness = useCallback(async () => {
    setReadinessLoading(true);
    setReadinessError("");
    try {
      const result = await getReadiness();
      setReadiness(result);
    } catch (error) {
      setReadinessError(error instanceof Error ? error.message : "Unable to contact Search AI.");
      setReadiness(null);
    } finally {
      setReadinessLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  const run = useCallback(async (nextPage = 1, explicitQuery?: string) => {
    const searchText = (explicitQuery ?? query).trim();
    if (!searchText) {
      setSearchError("Enter a search query first.");
      return;
    }

    setSearchLoading(true);
    setSearchError("");
    setShowRaw(false);
    const startedAt = performance.now();
    try {
      const result = await runSearch({ query: searchText, page: nextPage, pageSize, kind });
      setSearchData(result);
      setSearchedQuery(searchText);
      setPage(nextPage);
      setDurationMs(Math.round(performance.now() - startedAt));
      setHistory((current) => [searchText, ...current.filter((item) => item !== searchText)].slice(0, 5));
    } catch (error) {
      setSearchData(null);
      setSearchError(error instanceof Error ? error.message : "Search failed. Please try again.");
    } finally {
      setSearchLoading(false);
    }
  }, [kind, pageSize, query]);

  const totalPages = useMemo(() => {
    const total = searchData?.TotalCount ?? 0;
    return Math.max(1, Math.ceil(total / pageSize));
  }, [pageSize, searchData?.TotalCount]);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void run(1);
  };

  const serviceStatus = readinessLabel(readiness, readinessLoading, readinessError);
  const serviceReady = readiness?.status === "ready";
  const totalCount = searchData?.TotalCount ?? 0;
  const results = searchData?.Data ?? [];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#playground" aria-label="BONC Search AI Admin home">
          <span className="brand-mark">B</span>
          <span className="brand-copy"><strong>BONC</strong><small>SEARCH AI</small></span>
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <a className="nav-item active" href="#playground"><span className="nav-icon">⌕</span><span>Search Playground</span></a>
        <div className="nav-item disabled" title="Planned for a later phase"><span className="nav-icon">⚙</span><span>Search rules</span><span className="soon">Later</span></div>
        <div className="nav-item disabled" title="Planned for a later phase"><span className="nav-icon">✦</span><span>Feedback</span><span className="soon">Later</span></div>
        <div className="nav-item disabled" title="Planned for a later phase"><span className="nav-icon">▤</span><span>Index status</span><span className="soon">Later</span></div>
        <div className="sidebar-bottom">
          <span className="env-dot" />
          <div><strong>Development</strong><small>Local Search AI service</small></div>
        </div>
      </aside>

      <main className="main-content" id="playground">
        <header className="topbar">
          <div className="breadcrumbs"><span>Search AI</span><span className="crumb-sep">/</span><strong>Playground</strong></div>
          <div className={`service-pill ${serviceReady ? "ready" : readinessError ? "down" : "pending"}`}>
            <span className="status-dot" />
            <span>{serviceStatus}</span>
            <button className="icon-button" onClick={() => void refreshReadiness()} title="Refresh service status" aria-label="Refresh service status">↻</button>
          </div>
        </header>

        <section className="page-heading">
          <div>
            <div className="eyebrow">SEARCH QUALITY WORKSPACE</div>
            <h1>Search Playground</h1>
            <p>Test real Search AI results, inspect matching signals, and find queries that need improvement.</p>
          </div>
          <div className="api-chip"><span className="api-dot" /> API <code>{SEARCH_API_BASE}/api/search</code></div>
        </section>

        {!serviceReady && (
          <section className={`notice ${readinessError ? "notice-error" : "notice-info"}`}>
            <div className="notice-icon">{readinessError ? "!" : "i"}</div>
            <div>
              <strong>{readinessError ? "Search AI could not be reached" : "Search AI is still initializing"}</strong>
              <p>{readinessError || "You can still view this page, but search requests may fail until the model and indexes are ready."}</p>
            </div>
          </section>
        )}

        {readiness?.checks?.indexes && <section className="index-strip" aria-label="Search index status">
          {Object.entries(readiness.checks.indexes).map(([key, snapshot]) => <div className="index-cell" key={key}>
            <div><span className="index-label">{indexLabel(key)}</span><strong>{typeof snapshot.count === "number" ? snapshot.count.toLocaleString() : "—"}</strong></div>
            <span className={`index-state ${snapshot.status === "ready" || snapshot.status === "ready_empty" || snapshot.index_built ? "ok" : "wait"}`}>
              {textValue(snapshot.status) || (snapshot.index_built ? "ready" : "pending")}
            </span>
          </div>)}
        </section>}

        <section className="panel search-panel">
          <div className="panel-title-row">
            <div><span className="section-kicker">01 / QUERY</span><h2>Run a search</h2></div>
            <span className="method-badge">POST · /api/search</span>
          </div>
          <form onSubmit={onSubmit}>
            <label className="field-label" htmlFor="query">Search query</label>
            <div className="search-input-wrap">
              <span className="search-glyph">⌕</span>
              <input id="query" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} placeholder="e.g. industrial diesel generator supplier" autoComplete="off" />
              <span className="char-count">{query.length}/200</span>
            </div>
            <div className="controls-row">
              <div className="control-field">
                <label className="field-label" htmlFor="kind">Search in</label>
                <select id="kind" value={kind} onChange={(event) => { setKind(event.target.value as SearchKind); setPage(1); setSearchData(null); setSearchedQuery(""); }}>
                  <option value="all">All results</option>
                  <option value="business">Businesses</option>
                  <option value="product">Products</option>
                </select>
              </div>
              <div className="control-field size-field">
                <label className="field-label" htmlFor="pageSize">Results per page</label>
                <select id="pageSize" value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); setSearchData(null); setSearchedQuery(""); }}>
                  {PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} results</option>)}
                </select>
              </div>
              <button className="primary-button" type="submit" disabled={searchLoading || !query.trim()}>
                {searchLoading ? <><span className="spinner" /> Searching…</> : <><span>⌕</span> Run search</>}
              </button>
            </div>
          </form>
          <div className="examples-block">
            <span className="field-label">Try an example</span>
            <div className="example-chips">
              {EXAMPLE_QUERIES.map((example) => <button key={example} type="button" className="example-chip" onClick={() => setQuery(example)}>{example}<span>↗</span></button>)}
            </div>
          </div>
          {history.length > 0 && <div className="history-block"><span className="field-label">Recent in this session</span><div className="history-items">{history.map((item) => <button type="button" key={item} onClick={() => { setQuery(item); void run(1, item); }}>{item}</button>)}</div></div>}
          {searchError && <div className="inline-error"><strong>Search failed</strong><span>{searchError}</span></div>}
        </section>

        <section className="results-section">
          <div className="results-heading">
            <div><span className="section-kicker">02 / RESULTS</span><h2>{searchData ? "Search results" : "Results will appear here"}</h2></div>
            {searchData && <div className="result-metrics"><div><strong>{totalCount.toLocaleString()}</strong><span>Total results</span></div><div><strong>{durationMs ?? "—"}<small>{durationMs === null ? "" : " ms"}</small></strong><span>Response time</span></div></div>}
          </div>

          {!searchData && !searchLoading && !searchError && <div className="empty-state"><div className="empty-icon">⌕</div><h3>Start with a search query</h3><p>Results from the existing Search AI API will show here, with each record's type, score, and matching information.</p></div>}
          {searchLoading && <div className="loading-state"><span className="spinner dark" /> Searching the current catalogue…</div>}
          {searchData && !searchLoading && results.length === 0 && <div className="empty-state compact"><div className="empty-icon">∅</div><h3>No results for this query</h3><p>Try a shorter query, another search type, or a different spelling.</p></div>}

          {results.length > 0 && !searchLoading && <>
            <div className="result-list">
              {results.map((item, index) => {
                const title = resultTitle(item);
                const location = resultLocation(item);
                const category = resultCategory(item);
                const description = resultDescription(item);
                const rank = (page - 1) * pageSize + index + 1;
                const kindName = kindLabel(item.type);
                return <article className="result-card" key={`${String(item.id ?? item._id ?? title)}-${rank}`}>
                  <div className="rank">{String(rank).padStart(2, "0")}</div>
                  <div className="result-main">
                    <div className="result-title-line"><span className={`type-tag ${kindName.toLowerCase()}`}>{kindName}</span><h3>{title}</h3></div>
                    <div className="result-meta">
                      {category && <span>{category}</span>}
                      {textValue(item.model) && <span>Model: {textValue(item.model)}</span>}
                      {location && <span>⌖ {location}</span>}
                      {textValue(item.slug) && <span className="slug">/{textValue(item.slug)}</span>}
                    </div>
                    {description && <p className="result-description">{description.length > 320 ? `${description.slice(0, 320)}…` : description}</p>}
                    <div className="signal-row">
                      {textValue(item.matchType) && <span className="signal"><span className="signal-dot" />{textValue(item.matchType)}</span>}
                      {textValue(item.matchedKeyword) && <span className="matched-keyword">Matched: {textValue(item.matchedKeyword)}</span>}
                    </div>
                  </div>
                  <div className="score-block"><strong>{scoreLabel(item.matchPercentage)}</strong><span>Match score</span></div>
                </article>;
              })}
            </div>
            <div className="results-footer">
              <span>Page {page} of {totalPages} · Scores are ranking signals, not probabilities.</span>
              <div className="pagination-controls">
                <button type="button" className="secondary-button" disabled={page <= 1 || searchLoading} onClick={() => void run(page - 1, searchedQuery)}>← Previous</button>
                <button type="button" className="secondary-button" disabled={page >= totalPages || searchLoading} onClick={() => void run(page + 1, searchedQuery)}>Next →</button>
              </div>
            </div>
            <div className="raw-toggle-row"><button type="button" className="text-button" onClick={() => setShowRaw((value) => !value)}>{showRaw ? "Hide" : "Inspect"} raw API response <span>{showRaw ? "−" : "+"}</span></button></div>
            {showRaw && <pre className="raw-json">{JSON.stringify(searchData, null, 2)}</pre>}
          </>}
        </section>

        <section className="bottom-grid">
          <div className="mini-panel"><div className="mini-icon">i</div><div><strong>How to read match scores</strong><p>Scores are generated by the current search logic. They are not calibrated confidence probabilities, so compare actual relevance before changing a rule.</p></div></div>
          <div className="mini-panel"><div className="mini-icon">↻</div><div><strong>Current model stays unchanged</strong><p>This playground only tests search. It does not train the embedding model or modify search settings.</p></div></div>
        </section>

        <footer className="footer"><span>BONC · Search AI Admin</span><span>Standalone frontend · No BrochureIQ UI dependencies</span></footer>
      </main>
    </div>
  );
}

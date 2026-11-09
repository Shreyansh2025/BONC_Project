import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  getReadiness,
  getSearchSettings,
  getSearchSettingsHistory,
  runSearch,
  SEARCH_API_BASE,
  updateSearchSettings,
  type ReadinessResponse,
  type SearchKind,
  type SearchResponse,
  type SearchResult,
  type SearchRulesSettings,
  type SearchSettingsHistoryItem,
  type SearchSettingsRecord,
} from "./lib/search-api";

type ActiveTab = "playground" | "settings" | "indexes";
const PAGE_SIZE_OPTIONS = [10, 25, 50];
const EXAMPLE_QUERIES = [
  "diesel generator",
  "industrial equipment supplier",
  "generator in Indore",
  "1250kW generator",
];
const DEFAULT_SETTINGS: SearchRulesSettings = {
  semantic_candidate_limit: 50,
  lexical_result_threshold: 20,
  company: { semantic_min_score: 35, semantic_high_confidence_score: 60 },
  b2b_product: { semantic_min_score: 40, semantic_high_confidence_score: 65 },
  local_product: { semantic_min_score: 10 },
};

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

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function cloneSettings(settings: SearchRulesSettings): SearchRulesSettings {
  return JSON.parse(JSON.stringify(settings)) as SearchRulesSettings;
}

export default function App() {
  const [activeTab, setActiveTab] = useState<ActiveTab>("playground");
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

  const [settingsRecord, setSettingsRecord] = useState<SearchSettingsRecord | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<SearchRulesSettings | null>(null);
  const [settingsHistory, setSettingsHistory] = useState<SearchSettingsHistoryItem[]>([]);
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [settingsSuccess, setSettingsSuccess] = useState("");
  const [changeReason, setChangeReason] = useState("");

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

  const refreshSettings = useCallback(async () => {
    setSettingsLoading(true);
    setSettingsError("");
    setSettingsSuccess("");
    try {
      const [record, items] = await Promise.all([getSearchSettings(), getSearchSettingsHistory(50)]);
      setSettingsRecord(record);
      setSettingsDraft(cloneSettings(record.settings));
      setSettingsHistory(items);
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Unable to load admin settings.");
    } finally {
      setSettingsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  useEffect(() => {
    if (activeTab === "settings") void refreshSettings();
  }, [activeTab, refreshSettings]);

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

  const saveSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!settingsRecord || !settingsDraft) return;
    setSettingsSaving(true);
    setSettingsError("");
    setSettingsSuccess("");
    try {
      const saved = await updateSearchSettings({
        expectedVersion: settingsRecord.version,
        settings: settingsDraft,
        changeReason,
      });
      setSettingsRecord(saved);
      setSettingsDraft(cloneSettings(saved.settings));
      setChangeReason("");
      setSettingsHistory(await getSearchSettingsHistory(50));
      setSettingsSuccess(`Settings saved as version ${saved.version}. Changes apply to subsequent searches.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unable to save settings.";
      setSettingsError(message);
      if (message.toLowerCase().includes("version")) {
        setSettingsError(`${message} Reload the current settings before trying again.`);
      }
    } finally {
      setSettingsSaving(false);
    }
  };

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
  const pageTitle = activeTab === "settings" ? "Search rules" : activeTab === "indexes" ? "Index status" : "Search Playground";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#playground" aria-label="BONC Search AI Admin home" onClick={() => setActiveTab("playground")}>
          <span className="brand-mark">B</span>
          <span className="brand-copy"><strong>BONC</strong><small>SEARCH AI</small></span>
        </a>
        <div className="sidebar-label">WORKSPACE</div>
        <button className={`nav-item ${activeTab === "playground" ? "active" : ""}`} onClick={() => setActiveTab("playground")}><span className="nav-icon">⌕</span><span>Search Playground</span></button>
        <button className={`nav-item ${activeTab === "settings" ? "active" : ""}`} onClick={() => setActiveTab("settings")}><span className="nav-icon">⚙</span><span>Search rules</span></button>
        <button className="nav-item disabled" disabled title="Feedback collection is not implemented yet"><span className="nav-icon">✦</span><span>Feedback</span><span className="soon">Later</span></button>
        <button className={`nav-item ${activeTab === "indexes" ? "active" : ""}`} onClick={() => setActiveTab("indexes")}><span className="nav-icon">▤</span><span>Index status</span></button>
        <div className="sidebar-bottom">
          <span className="env-dot" />
          <div><strong>Local Admin UI</strong><small>Search AI API via proxy</small></div>
        </div>
      </aside>

      <main className="main-content" id="playground">
        <header className="topbar">
          <div className="breadcrumbs"><span>Search AI</span><span className="crumb-sep">/</span><strong>{pageTitle}</strong></div>
          <div className={`service-pill ${serviceReady ? "ready" : readinessError ? "down" : "pending"}`}>
            <span className="status-dot" />
            <span>{serviceStatus}</span>
            <button className="icon-button" onClick={() => void refreshReadiness()} title="Refresh service status" aria-label="Refresh service status">↻</button>
          </div>
        </header>

        <section className="page-heading">
          <div>
            <div className="eyebrow">SEARCH QUALITY WORKSPACE</div>
            <h1>{pageTitle}</h1>
            <p>{activeTab === "settings" ? "Review and safely update the persisted rules used by Search AI." : activeTab === "indexes" ? "Check database readiness, model readiness, and the current in-memory index counts." : "Test real Search AI results, inspect matching signals, and find queries that need improvement."}</p>
          </div>
          <div className="api-chip"><span className="api-dot" /> API <code>{SEARCH_API_BASE}</code></div>
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

        {activeTab === "playground" && <>
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
            <div className="mini-panel"><div className="mini-icon">↻</div><div><strong>Current model stays unchanged</strong><p>This playground tests search. It does not train or replace the embedding model.</p></div></div>
          </section>
        </>}

        {activeTab === "settings" && <>
          {settingsError && <div className="inline-error settings-alert"><strong>Settings unavailable</strong><span>{settingsError}</span><button type="button" className="secondary-button" onClick={() => void refreshSettings()}>Retry</button></div>}
          {settingsSuccess && <div className="success-message">{settingsSuccess}</div>}
          {settingsLoading && <div className="loading-state"><span className="spinner dark" /> Loading saved settings and change history…</div>}
          {!settingsLoading && settingsDraft && settingsRecord && <>
            <section className="settings-summary">
              <div><span className="section-kicker">ACTIVE CONFIGURATION</span><strong>Version {settingsRecord.version}</strong></div>
              <div><span>Last updated</span><strong>{formatDate(settingsRecord.updated_at_utc)}</strong></div>
              <div><span>Updated by</span><strong>{settingsRecord.updated_by || "—"}</strong></div>
            </section>
            <section className="panel settings-panel">
              <div className="panel-title-row"><div><span className="section-kicker">01 / RULES</span><h2>Search thresholds</h2></div><span className="method-badge">Validated · Versioned</span></div>
              <p className="panel-intro">These values are persisted by the backend and applied to subsequent search requests. Keep changes small and test them in the Playground.</p>
              <form onSubmit={(event) => void saveSettings(event)}>
                <div className="settings-grid">
                  <label className="setting-field"><span>Semantic candidate limit</span><small>FAISS candidates to retrieve for each semantic search (10–100).</small><input type="number" min="10" max="100" step="1" value={settingsDraft.semantic_candidate_limit} onChange={(event) => setSettingsDraft({ ...settingsDraft, semantic_candidate_limit: Number(event.target.value) })} /></label>
                  <label className="setting-field"><span>Lexical result threshold</span><small>Run semantic search when lexical matches are below this count (1–100).</small><input type="number" min="1" max="100" step="1" value={settingsDraft.lexical_result_threshold} onChange={(event) => setSettingsDraft({ ...settingsDraft, lexical_result_threshold: Number(event.target.value) })} /></label>
                </div>
                <div className="settings-section-title"><div><h3>Company search</h3><p>Semantic acceptance and high-confidence cutoff.</p></div></div>
                <div className="settings-grid">
                  <label className="setting-field"><span>Semantic minimum score</span><small>Discard company semantic matches below this score (0–100).</small><input type="number" min="0" max="100" step="0.5" value={settingsDraft.company.semantic_min_score} onChange={(event) => setSettingsDraft({ ...settingsDraft, company: { ...settingsDraft.company, semantic_min_score: Number(event.target.value) } })} /></label>
                  <label className="setting-field"><span>High-confidence score</span><small>At or above this score, the shared-word check is not required.</small><input type="number" min="0" max="100" step="0.5" value={settingsDraft.company.semantic_high_confidence_score} onChange={(event) => setSettingsDraft({ ...settingsDraft, company: { ...settingsDraft.company, semantic_high_confidence_score: Number(event.target.value) } })} /></label>
                </div>
                <div className="settings-section-title"><div><h3>B2B catalogue product search</h3><p>Rules for published catalogue products.</p></div></div>
                <div className="settings-grid">
                  <label className="setting-field"><span>Semantic minimum score</span><small>Discard B2B product semantic matches below this score (0–100).</small><input type="number" min="0" max="100" step="0.5" value={settingsDraft.b2b_product.semantic_min_score} onChange={(event) => setSettingsDraft({ ...settingsDraft, b2b_product: { ...settingsDraft.b2b_product, semantic_min_score: Number(event.target.value) } })} /></label>
                  <label className="setting-field"><span>High-confidence score</span><small>At or above this score, the shared-word check is not required.</small><input type="number" min="0" max="100" step="0.5" value={settingsDraft.b2b_product.semantic_high_confidence_score} onChange={(event) => setSettingsDraft({ ...settingsDraft, b2b_product: { ...settingsDraft.b2b_product, semantic_high_confidence_score: Number(event.target.value) } })} /></label>
                </div>
                <div className="settings-section-title"><div><h3>BrochureIQ local products</h3><p>Rules for products extracted from uploaded brochures.</p></div></div>
                <div className="settings-grid single-field">
                  <label className="setting-field"><span>Semantic minimum score</span><small>Discard local product semantic matches below this score (0–100).</small><input type="number" min="0" max="100" step="0.5" value={settingsDraft.local_product.semantic_min_score} onChange={(event) => setSettingsDraft({ ...settingsDraft, local_product: { semantic_min_score: Number(event.target.value) } })} /></label>
                </div>
                <label className="setting-field reason-field"><span>Change reason <em>Optional</em></span><input type="text" maxLength={1000} value={changeReason} onChange={(event) => setChangeReason(event.target.value)} placeholder="e.g. Adjusted after reviewing staging search results" /></label>
                <div className="settings-actions"><button type="button" className="secondary-button" onClick={() => { setSettingsDraft(cloneSettings(DEFAULT_SETTINGS)); setSettingsSuccess(""); }}>Load defaults</button><span className="action-spacer" /><button type="submit" className="primary-button" disabled={settingsSaving}>{settingsSaving ? <><span className="spinner" /> Saving…</> : "Save new version"}</button></div>
                <p className="settings-footnote">Saving creates a new version and an audit-history entry. High-confidence scores must remain greater than their respective minimum scores.</p>
              </form>
            </section>

            <section className="panel history-panel">
              <div className="panel-title-row"><div><span className="section-kicker">02 / AUDIT</span><h2>Settings history</h2></div><button type="button" className="secondary-button" onClick={() => void refreshSettings()}>Refresh history</button></div>
              {settingsHistory.length === 0 ? <p className="panel-intro">No saved history entries were returned.</p> : <div className="settings-history-list">
                {settingsHistory.map((item) => <article className="settings-history-item" key={item.history_id}>
                  <div className="history-version"><strong>v{item.version}</strong><small>{formatDate(item.changed_at_utc)}</small></div>
                  <div className="history-details"><strong>{item.changed_by || "Unknown admin"}</strong><p>{item.change_reason || "No change reason supplied."}</p></div>
                  <button type="button" className="secondary-button" onClick={() => { setSettingsDraft(cloneSettings(item.settings)); setChangeReason(`Based on version ${item.version}`); setSettingsSuccess(`Version ${item.version} loaded into the form. Save to create a new version.`); window.scrollTo({ top: 0, behavior: "smooth" }); }}>Load version</button>
                </article>)}
              </div>}
              <p className="settings-footnote">Loading an older version only copies its values into the form. Saving creates a new version; the audit history is never overwritten.</p>
            </section>
          </>}
        </>}

        {activeTab === "indexes" && <>
          <section className="status-grid">
            <div className="status-card"><span>Database</span><strong className={readiness?.checks?.database === "ready" ? "status-good" : "status-muted"}>{readiness?.checks?.database || "—"}</strong><small>SQL Server connection status</small></div>
            <div className="status-card"><span>Embedding model</span><strong className={readiness?.checks?.embeddingModel === "ready" ? "status-good" : "status-muted"}>{readiness?.checks?.embeddingModel || "—"}</strong><small>Model warm-up status</small></div>
            <div className="status-card"><span>Overall service</span><strong className={serviceReady ? "status-good" : "status-muted"}>{readiness?.status || (readinessLoading ? "checking" : "unknown")}</strong><small>Readiness endpoint</small></div>
          </section>
          <section className="panel index-status-panel">
            <div className="panel-title-row"><div><span className="section-kicker">01 / INDEXES</span><h2>In-memory search indexes</h2></div><button type="button" className="secondary-button" onClick={() => void refreshReadiness()}>Refresh status</button></div>
            {readiness?.checks?.indexes ? <div className="index-status-list">{Object.entries(readiness.checks.indexes).map(([key, snapshot]) => <article className="index-status-item" key={key}>
              <div><strong>{indexLabel(key)}</strong><small>{snapshot.index_built ? "Index built" : "Index not built"}</small></div>
              <strong className="index-count">{typeof snapshot.count === "number" ? snapshot.count.toLocaleString() : "—"}<small> records</small></strong>
              <span className={`index-state ${snapshot.status === "ready" || snapshot.status === "ready_empty" ? "ok" : "wait"}`}>{snapshot.status || "unknown"}</span>
              {snapshot.last_error && <p className="index-error">Last error: {snapshot.last_error}</p>}
            </article>)}</div> : <p className="panel-intro">Index details are not available until Search AI responds to its readiness check.</p>}
            {readiness?.initializationError && <div className="inline-error"><strong>Initialization error</strong><span>{readiness.initializationError}</span></div>}
          </section>
          <p className="settings-footnote">An index with status <strong>ready_empty</strong> means its source table is currently empty. This is not the same as an index build failure.</p>
        </>}

        <footer className="footer"><span>BONC · Search AI Admin</span><span>Standalone frontend · No BrochureIQ UI dependencies</span></footer>
      </main>
    </div>
  );
}

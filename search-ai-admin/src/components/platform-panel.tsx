import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  createPlatformCategory,
  getPlatformCategories,
  getPlatformCategoryHistory,
  ingestPlatformDocuments,
  runPlatformAdminSearch,
  syncPlatformCategory,
  updatePlatformCategory,
  type PlatformCategoryConfig,
  type PlatformCategoryRecord,
  type PlatformCategoryHistoryItem,
  type PlatformFieldConfig,
  type PlatformFieldRole,
  type PlatformSearchResponse,
} from "../lib/search-api";

const ROLE_OPTIONS: PlatformFieldRole[] = ["title", "skill", "location", "category", "description", "general"];

const INITIAL_FIELDS: PlatformFieldConfig[] = [
  { key: "name", source_column: "FullName", label: "Name", role: "title", weight: 5, searchable: true, semantic: true, aliases: ["person", "user"] },
  { key: "skills", source_column: "Skills", label: "Skills", role: "skill", weight: 4, searchable: true, semantic: true, aliases: ["expertise", "technology"] },
  { key: "bio", source_column: "Bio", label: "Biography", role: "description", weight: 2, searchable: true, semantic: true, aliases: ["about", "experience"] },
  { key: "location", source_column: "City", label: "Location", role: "location", weight: 1, searchable: true, semantic: true, aliases: ["city", "place"] },
];

const DEFAULT_RULES = {
  lexical_enabled: true,
  semantic_enabled: true,
  semantic_min_score: 20,
  semantic_candidate_limit: 50,
  lexical_weight: 0.65,
  semantic_weight: 0.35,
};

function getError(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export default function PlatformPanel() {
  const [categories, setCategories] = useState<PlatformCategoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [syncing, setSyncing] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [categoryId, setCategoryId] = useState("people");
  const [displayName, setDisplayName] = useState("People");
  const [description, setDescription] = useState("Search BONC people profiles");
  const [sourceType, setSourceType] = useState<"sqlserver" | "push">("sqlserver");
  const [connectionEnv, setConnectionEnv] = useState("SEARCH_SOURCE_PEOPLE");
  const [schema, setSchema] = useState("dbo");
  const [table, setTable] = useState("Users");
  const [idColumn, setIdColumn] = useState("UserId");
  const [maxRecords, setMaxRecords] = useState(50000);
  const [publicEnabled, setPublicEnabled] = useState(false);
  const [fields, setFields] = useState<PlatformFieldConfig[]>(INITIAL_FIELDS);
  const [minScore, setMinScore] = useState(20);
  const [lexicalWeight, setLexicalWeight] = useState(0.65);
  const [semanticWeight, setSemanticWeight] = useState(0.35);
  const [lexicalEnabled, setLexicalEnabled] = useState(true);
  const [semanticEnabled, setSemanticEnabled] = useState(true);
  const [candidateLimit, setCandidateLimit] = useState(50);
  const [editingVersion, setEditingVersion] = useState<number | null>(null);

  const [selectedCategory, setSelectedCategory] = useState("");
  const [rawDocuments, setRawDocuments] = useState('[\n  {\n    "document_id": "person-1",\n    "title": "Shreyansh",\n    "fields": { "name": "Shreyansh", "skills": "OCR, Python", "bio": "Document processing developer", "location": "Indore" }\n  }\n]');
  const [query, setQuery] = useState("Shreyansh OCR expert in Indore");
  const [searchResult, setSearchResult] = useState<PlatformSearchResponse | null>(null);
  const [categoryHistory, setCategoryHistory] = useState<PlatformCategoryHistoryItem[]>([]);
  const [changeReason, setChangeReason] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getPlatformCategories();
      setCategories(result);
      if (!selectedCategory && result.length) setSelectedCategory(result[0].config.category_id);
    } catch (e) {
      setError(getError(e, "Unable to load categories. Apply migration 004 and check the Search AI API."));
    } finally {
      setLoading(false);
    }
  }, [selectedCategory]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selectedRecord = useMemo(
    () => categories.find((item) => item.config.category_id === selectedCategory) ?? null,
    [categories, selectedCategory],
  );

  const loadHistory = async (id: string) => {
    try {
      setCategoryHistory(await getPlatformCategoryHistory(id));
    } catch {
      setCategoryHistory([]);
    }
  };

  const updateField = (index: number, patch: Partial<PlatformFieldConfig>) => {
    setFields((current) => current.map((field, i) => i === index ? { ...field, ...patch } : field));
  };

  const addField = () => {
    const number = fields.length + 1;
    setFields((current) => [...current, {
      key: "field_" + number,
      source_column: "Column" + number,
      label: "Field " + number,
      role: "general",
      weight: 1,
      searchable: true,
      semantic: true,
      aliases: [],
    }]);
  };

  const removeField = (index: number) => {
    setFields((current) => current.filter((_, i) => i !== index));
  };

  const loadCategoryForEdit = (item: PlatformCategoryRecord) => {
    const config = item.config;
    setCategoryId(config.category_id);
    setDisplayName(config.display_name);
    setDescription(config.description || "");
    setSourceType(config.source.type);
    if (config.source.type === "sqlserver") {
      setConnectionEnv(config.source.connection_env);
      setSchema(config.source.schema);
      setTable(config.source.table);
      setIdColumn(config.source.id_column);
      setMaxRecords(config.source.max_records);
    }
    setFields(config.fields.map((field) => ({ ...field, aliases: [...field.aliases] })));
    setMinScore(config.rules.semantic_min_score);
    setLexicalWeight(config.rules.lexical_weight);
    setSemanticWeight(config.rules.semantic_weight);
    setLexicalEnabled(config.rules.lexical_enabled);
    setSemanticEnabled(config.rules.semantic_enabled);
    setCandidateLimit(config.rules.semantic_candidate_limit);
    setPublicEnabled(config.public_search_enabled);
    setEditingVersion(item.config_version);
    setSelectedCategory(config.category_id);
    void loadHistory(config.category_id);
    setSearchResult(null);
    setNotice("Editing " + config.display_name + ". Save changes to create a new configuration version.");
    setError("");
  };

  const createCategory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const config: PlatformCategoryConfig = {
        category_id: categoryId.trim().toLowerCase(),
        display_name: displayName.trim(),
        description: description.trim(),
        source: sourceType === "push"
          ? { type: "push" }
          : {
              type: "sqlserver",
              connection_env: connectionEnv.trim(),
              schema: schema.trim(),
              table: table.trim(),
              id_column: idColumn.trim(),
              max_records: maxRecords,
              filters: [],
            },
        fields,
        rules: {
          ...DEFAULT_RULES,
          lexical_enabled: lexicalEnabled,
          semantic_enabled: semanticEnabled,
          semantic_min_score: minScore,
          semantic_candidate_limit: candidateLimit,
          lexical_weight: lexicalWeight,
          semantic_weight: semanticWeight,
        },
        active: true,
        public_search_enabled: publicEnabled,
      };
      const saved = editingVersion === null
        ? await createPlatformCategory(config)
        : await updatePlatformCategory({
            categoryId: config.category_id,
            expectedVersion: editingVersion,
            config,
            changeReason,
          });
      setNotice(editingVersion === null
        ? "Category created. Next, sync the SQL source or ingest documents."
        : "Category configuration updated. Re-sync or re-ingest data to rebuild the index.");
      setEditingVersion(null);
      setChangeReason("");
      setSelectedCategory(saved.config.category_id);
      await loadHistory(saved.config.category_id);
      await refresh();
    } catch (e) {
      setError(getError(e, "Unable to create category."));
    } finally {
      setSaving(false);
    }
  };

  const syncCategory = async (id: string) => {
    setSyncing(id);
    setError("");
    setNotice("");
    try {
      const result = await syncPlatformCategory(id);
      setNotice("Synced " + result.documents_synced.toLocaleString() + " records into " + id + ".");
      await refresh();
    } catch (e) {
      setError(getError(e, "Unable to sync the configured source."));
    } finally {
      setSyncing("");
    }
  };

  const ingest = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedCategory) return;
    setIngesting(true);
    setError("");
    setNotice("");
    try {
      const parsed = JSON.parse(rawDocuments);
      if (!Array.isArray(parsed)) throw new Error("The JSON must be an array of document objects.");
      const result = await ingestPlatformDocuments(selectedCategory, parsed, true);
      setNotice("Ingested " + result.documents_ingested.toLocaleString() + " documents into " + selectedCategory + ".");
    } catch (e) {
      setError(getError(e, "Unable to ingest documents. Check the JSON format."));
    } finally {
      setIngesting(false);
    }
  };

  const runTestSearch = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedCategory) return;
    setSearching(true);
    setError("");
    setSearchResult(null);
    try {
      const result = await runPlatformAdminSearch({ query, category: selectedCategory });
      setSearchResult(result);
    } catch (e) {
      setError(getError(e, "Search test failed. Ensure the category has been ingested or synced."));
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="platform-workspace">
      <section className="panel platform-intro-panel">
        <div className="panel-title-row">
          <div><span className="section-kicker">NEW / CONFIGURATION-DRIVEN SEARCH</span><h2>Search categories</h2></div>
          <button type="button" className="secondary-button" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
        </div>
        <p className="panel-intro">Create a category with its own source, field mapping and ranking settings. The generic search pipeline runs separately from the existing Company and Product search.</p>
        {error && <div className="inline-error"><strong>Action failed</strong><span>{error}</span></div>}
        {notice && <div className="success-message">{notice}</div>}
        <div className="platform-category-list">
          {loading ? <p className="panel-intro">Loading categories…</p> : categories.length === 0 ? <p className="panel-intro">No categories created yet.</p> : categories.map((item) => (
            <article className="platform-category-card" key={item.config.category_id}>
              <div>
                <strong>{item.config.display_name}</strong>
                <small>{item.config.category_id} · {item.config.source.type} · config v{item.config_version} · index v{item.index_version}</small>
                <small>{item.config.fields.length} mapped fields · {item.config.public_search_enabled ? "public search enabled" : "admin-only"}</small>
              </div>
              <div className="platform-category-actions">
                <button type="button" className="secondary-button" onClick={() => { setSelectedCategory(item.config.category_id); void loadHistory(item.config.category_id); }}>Test</button>
                <button type="button" className="secondary-button" onClick={() => loadCategoryForEdit(item)}>Edit</button>
                {item.config.source.type === "sqlserver" && <button type="button" className="secondary-button" disabled={syncing === item.config.category_id} onClick={() => void syncCategory(item.config.category_id)}>{syncing === item.config.category_id ? "Syncing…" : "Sync source"}</button>}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel platform-form-panel">
        <div className="panel-title-row"><div><span className="section-kicker">01 / CATEGORY SETUP</span><h2>{editingVersion === null ? "Create a category" : "Edit category"}</h2></div><span className="method-badge">Admin API</span></div>
        <form onSubmit={(event) => void createCategory(event)}>
          <div className="platform-form-grid">
            <label className="setting-field"><span>Category ID</span><small>Lowercase identifier, e.g. people or articles.</small><input required value={categoryId} onChange={(e) => setCategoryId(e.target.value)} pattern="[a-z][a-z0-9_-]{1,62}" /></label>
            <label className="setting-field"><span>Display name</span><input required value={displayName} onChange={(e) => setDisplayName(e.target.value)} /></label>
            <label className="setting-field wide"><span>Description</span><input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          </div>

          <div className="settings-section-title"><div><h3>Data source</h3><p>Credentials stay on the server. The browser stores only the name of a server-side connection setting.</p></div></div>
          <div className="platform-form-grid">
            <label className="setting-field"><span>Source type</span><select value={sourceType} onChange={(e) => setSourceType(e.target.value as "sqlserver" | "push")}><option value="sqlserver">SQL Server table</option><option value="push">Application pushes records</option></select></label>
            {sourceType === "sqlserver" ? <>
              <label className="setting-field"><span>Connection env variable</span><input value={connectionEnv} onChange={(e) => setConnectionEnv(e.target.value.toUpperCase())} pattern="SEARCH_SOURCE_[A-Z0-9_]{1,80}" /></label>
              <label className="setting-field"><span>Schema</span><input value={schema} onChange={(e) => setSchema(e.target.value)} required /></label>
              <label className="setting-field"><span>Table</span><input value={table} onChange={(e) => setTable(e.target.value)} required /></label>
              <label className="setting-field"><span>Primary key column</span><input value={idColumn} onChange={(e) => setIdColumn(e.target.value)} required /></label>
              <label className="setting-field"><span>Maximum records per sync</span><input type="number" min="1" max="100000" value={maxRecords} onChange={(e) => setMaxRecords(Number(e.target.value))} /></label>
            </> : <p className="panel-intro wide">Create the category, then select it below to push normalized records using the ingestion JSON form.</p>}
          </div>

          <div className="settings-section-title"><div><h3>Field mapping and weights</h3><p>Map source columns into standard searchable fields. Higher weights make a field contribute more to lexical relevance.</p></div><button type="button" className="secondary-button" onClick={addField}>Add field</button></div>
          <div className="platform-fields">
            {fields.map((field, index) => <div className="platform-field-row" key={index}>
              <label className="setting-field"><span>Field key</span><input value={field.key} onChange={(e) => updateField(index, { key: e.target.value })} required /></label>
              <label className="setting-field"><span>Source column</span><input value={field.source_column} onChange={(e) => updateField(index, { source_column: e.target.value })} required /></label>
              <label className="setting-field"><span>Label</span><input value={field.label} onChange={(e) => updateField(index, { label: e.target.value })} required /></label>
              <label className="setting-field"><span>Role</span><select value={field.role} onChange={(e) => updateField(index, { role: e.target.value as PlatformFieldRole })}>{ROLE_OPTIONS.map((role) => <option key={role} value={role}>{role}</option>)}</select></label>
              <label className="setting-field"><span>Weight</span><input type="number" min="0" max="100" step="0.5" value={field.weight} onChange={(e) => updateField(index, { weight: Number(e.target.value) })} /></label>
              <label className="setting-field"><span>Aliases (comma separated)</span><input value={field.aliases.join(", ")} onChange={(e) => updateField(index, { aliases: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></label>
              <label className="platform-checkbox"><input type="checkbox" checked={field.searchable} onChange={(e) => updateField(index, { searchable: e.target.checked })} /> Searchable</label>
              <label className="platform-checkbox"><input type="checkbox" checked={field.semantic} onChange={(e) => updateField(index, { semantic: e.target.checked })} /> Include in semantic text</label>
              <button type="button" className="secondary-button danger-button" onClick={() => removeField(index)} disabled={fields.length <= 1}>Remove</button>
            </div>)}
          </div>

          <div className="settings-section-title"><div><h3>Search rules</h3><p>These are runtime ranking configurations, not model fine-tuning.</p></div></div>
          <div className="platform-form-grid">
            <label className="setting-field"><span>Minimum semantic score</span><input type="number" min="0" max="100" step="0.5" value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} /></label>
            <label className="setting-field"><span>Semantic candidate limit</span><input type="number" min="1" max="200" value={candidateLimit} onChange={(e) => setCandidateLimit(Number(e.target.value))} /></label>
            <label className="setting-field"><span>Lexical weight</span><input type="number" min="0" max="1" step="0.05" value={lexicalWeight} onChange={(e) => setLexicalWeight(Number(e.target.value))} /></label>
            <label className="setting-field"><span>Semantic weight</span><input type="number" min="0" max="1" step="0.05" value={semanticWeight} onChange={(e) => setSemanticWeight(Number(e.target.value))} /></label>
            <label className="platform-checkbox"><input type="checkbox" checked={lexicalEnabled} onChange={(e) => setLexicalEnabled(e.target.checked)} /> Enable keyword matching</label>
            <label className="platform-checkbox"><input type="checkbox" checked={semanticEnabled} onChange={(e) => setSemanticEnabled(e.target.checked)} /> Enable semantic matching</label>
            <label className="platform-checkbox"><input type="checkbox" checked={publicEnabled} onChange={(e) => setPublicEnabled(e.target.checked)} /> Allow public search (only enable for approved non-sensitive data)</label>
            <label className="setting-field wide"><span>Change reason</span><input value={changeReason} onChange={(e) => setChangeReason(e.target.value)} maxLength={1000} placeholder="Why are you creating or changing this category?" /></label>
          </div>
          <div className="settings-actions"><span className="action-spacer" /><button type="submit" className="primary-button" disabled={saving || fields.length === 0}>{saving ? "Saving…" : editingVersion === null ? "Create category" : "Save category changes"}</button>
            {editingVersion !== null && <button type="button" className="secondary-button" onClick={() => setEditingVersion(null)}>Cancel edit</button>}</div>
        </form>
      </section>

      <section className="panel platform-test-panel">
        <div className="panel-title-row"><div><span className="section-kicker">02 / INGESTION & TEST</span><h2>Load and test category data</h2></div><span className="method-badge">Admin only</span></div>
        <div className="platform-form-grid">
          <label className="setting-field"><span>Category</span><select value={selectedCategory} onChange={(e) => { setSelectedCategory(e.target.value); setSearchResult(null); }}><option value="">Select a category</option>{categories.map((item) => <option key={item.config.category_id} value={item.config.category_id}>{item.config.display_name}</option>)}</select></label>
          <div className="setting-field"><span>Selected source</span><small>{selectedRecord ? selectedRecord.config.source.type + (selectedRecord.config.source.type === "sqlserver" ? " · " + selectedRecord.config.source.schema + "." + selectedRecord.config.source.table : "") : "No category selected"}</small></div>
        </div>
        {selectedRecord?.config.source.type === "push" && <form onSubmit={(event) => void ingest(event)}>
          <label className="setting-field"><span>Normalized documents (JSON array)</span><textarea className="platform-json-input" rows={9} value={rawDocuments} onChange={(e) => setRawDocuments(e.target.value)} spellCheck={false} /></label>
          <div className="settings-actions"><span className="action-spacer" /><button type="submit" className="primary-button" disabled={ingesting}>{ingesting ? "Ingesting…" : "Replace category documents"}</button></div>
        </form>}
        <form onSubmit={(event) => void runTestSearch(event)}>
          <div className="platform-form-grid">
            <label className="setting-field wide"><span>Natural-language test query</span><input value={query} onChange={(e) => setQuery(e.target.value)} required placeholder="e.g. Shreyansh OCR expert in Indore" /></label>
          </div>
          <div className="settings-actions"><span className="action-spacer" /><button type="submit" className="primary-button" disabled={!selectedCategory || searching}>{searching ? "Searching…" : "Test category search"}</button></div>
        </form>
        {categoryHistory.length > 0 && <div className="platform-history">
          <div className="settings-section-title"><div><h3>Configuration history</h3><p>Previous category configurations are kept as audit snapshots.</p></div></div>
          {categoryHistory.map((item) => <article className="platform-history-item" key={item.history_id}>
            <strong>v{item.config_version}</strong>
            <span>{item.changed_by || "Unknown admin"} · {item.status}</span>
            <small>{item.change_reason || "No reason supplied"}</small>
          </article>)}
        </div>}
        {searchResult && <div className="platform-results">
          <div className="platform-result-summary"><strong>{searchResult.total_count} results</strong><span>Index v{searchResult.index_version}</span></div>
          <div className="mini-panel"><div><strong>Query understanding (field hints)</strong><p>{pretty(searchResult.query_understanding)}</p></div></div>
          {searchResult.data.map((item) => <article className="platform-result-item" key={item.id}>
            <div><strong>{item.title}</strong><small>{item.matched_fields.join(", ") || "Semantic match"}</small><pre>{pretty(item.fields)}</pre></div>
            <strong>{item.score.toFixed(1)} pts</strong>
          </article>)}
          {searchResult.data.length === 0 && <p className="panel-intro">No matching documents in this category yet.</p>}
        </div>}
      </section>
    </div>
  );
}

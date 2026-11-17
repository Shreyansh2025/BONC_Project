import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  createPlatformCategory,
  discoverPlatformSourceSchema,
  suggestPlatformSourceMapping,
  previewPlatformJoinView,
  validatePlatformSource,
  getPlatformSourceProfiles,
  getPlatformCategories,
  getPlatformCategoryHistory,
  getPlatformFeedback,
  recordPlatformFeedback,
  ingestPlatformDocuments,
  runPlatformAdminSearch,
  syncPlatformCategory,
  updatePlatformCategory,
  type PlatformCategoryConfig,
  type PlatformCategoryRecord,
  type PlatformCategoryHistoryItem,
  type PlatformFeedbackSummary,
  type PlatformFieldConfig,
  type PlatformFieldRole,
  type PlatformSourceFilter,
  type PlatformSearchFilter,
  type PlatformSearchResponse,
  type PlatformSourceProfile,
  type PlatformSourceCatalog,
  type PlatformSourceMappingSuggestions,
  type PlatformSourceRelationship,
  type PlatformJoinViewPreview,
  type PlatformSourceValidation,
} from "../lib/search-api";

const ROLE_OPTIONS: PlatformFieldRole[] = ["title", "skill", "location", "category", "description", "general"];

const INITIAL_FIELDS: PlatformFieldConfig[] = [
  { key: "name", source_column: "FullName", label: "Name", role: "title", weight: 5, searchable: true, semantic: true, aliases: ["person", "user"] },
  { key: "skills", source_column: "Skills", label: "Skills", role: "skill", weight: 4, searchable: true, semantic: true, aliases: ["expertise", "technology"], filterable: true, data_type: "keyword", query_filter_mode: "preferred", query_match: "all" },
  { key: "bio", source_column: "Bio", label: "Biography", role: "description", weight: 2, searchable: true, semantic: true, aliases: ["about", "experience"] },
  { key: "location", source_column: "City", label: "Location", role: "location", weight: 1, searchable: true, semantic: true, aliases: ["city", "place"], filterable: true, data_type: "keyword", query_filter_mode: "preferred", query_match: "all" },
];

const DEFAULT_RULES = {
  lexical_enabled: true,
  semantic_enabled: true,
  semantic_min_score: 20,
  semantic_high_confidence_score: 75,
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

function parseSynonymGroups(value: string): Array<{ terms: string[] }> {
  return value.split(/\r?\n/)
    .map((line) => line.split(",").map((term) => term.trim().toLowerCase()).filter(Boolean))
    .filter((terms) => terms.length >= 2)
    .map((terms) => ({ terms }));
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
  const [connectionEnv, setConnectionEnv] = useState("");
  const [sourceProfiles, setSourceProfiles] = useState<PlatformSourceProfile[]>([]);
  const [sourceCatalog, setSourceCatalog] = useState<PlatformSourceCatalog | null>(null);
  const [discoveringSource, setDiscoveringSource] = useState(false);
  const [suggestingMapping, setSuggestingMapping] = useState(false);
  const [mappingSuggestions, setMappingSuggestions] = useState<PlatformSourceMappingSuggestions | null>(null);
  const [selectedJoinRelationships, setSelectedJoinRelationships] = useState<string[]>([]);
  const [viewSchema, setViewSchema] = useState("dbo");
  const [viewName, setViewName] = useState("vw_Search_People");
  const [viewPreview, setViewPreview] = useState<PlatformJoinViewPreview | null>(null);
  const [previewingView, setPreviewingView] = useState(false);
  const [validatingSource, setValidatingSource] = useState(false);
  const [sourceValidation, setSourceValidation] = useState<PlatformSourceValidation | null>(null);
  const [schema, setSchema] = useState("dbo");
  const [table, setTable] = useState("Users");
  const [idColumn, setIdColumn] = useState("UserId");
  const [maxRecords, setMaxRecords] = useState(50000);
  const [sourceFilters, setSourceFilters] = useState<PlatformSourceFilter[]>([]);
  const [publicEnabled, setPublicEnabled] = useState(false);
  const [fields, setFields] = useState<PlatformFieldConfig[]>(INITIAL_FIELDS);
  const [synonymText, setSynonymText] = useState("");
  const [minScore, setMinScore] = useState(20);
  const [highConfidenceScore, setHighConfidenceScore] = useState(75);
  const [lexicalWeight, setLexicalWeight] = useState(0.65);
  const [semanticWeight, setSemanticWeight] = useState(0.35);
  const [lexicalEnabled, setLexicalEnabled] = useState(true);
  const [semanticEnabled, setSemanticEnabled] = useState(true);
  const [candidateLimit, setCandidateLimit] = useState(50);
  const [editingVersion, setEditingVersion] = useState<number | null>(null);

  const [selectedCategory, setSelectedCategory] = useState("");
  const [rawDocuments, setRawDocuments] = useState('[\n  {\n    "document_id": "person-1",\n    "title": "Shreyansh",\n    "fields": { "name": "Shreyansh", "skills": "OCR, Python", "bio": "Document processing developer", "location": "Indore" }\n  }\n]');
  const [query, setQuery] = useState("Shreyansh OCR expert in Indore");
  const [searchFilters, setSearchFilters] = useState<PlatformSearchFilter[]>([]);
  const [searchResult, setSearchResult] = useState<PlatformSearchResponse | null>(null);
  const [categoryHistory, setCategoryHistory] = useState<PlatformCategoryHistoryItem[]>([]);
  const [feedbackSummary, setFeedbackSummary] = useState<PlatformFeedbackSummary | null>(null);
  const [feedbackSaving, setFeedbackSaving] = useState("");
  const [changeReason, setChangeReason] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await getPlatformCategories();
      setCategories(result);
      if (!selectedCategory && result.length) setSelectedCategory(result[0].config.category_id);
    } catch (e) {
      setError(getError(e, "Unable to load search engines. Apply migration 004 and check the Search AI API."));
    } finally {
      setLoading(false);
    }
  }, [selectedCategory]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    void getPlatformSourceProfiles().then((profiles) => {
      setSourceProfiles(profiles);
      setConnectionEnv((current) => current || (profiles[0] ? "SEARCH_SOURCE_" + profiles[0].profile_id : ""));
    }).catch(() => setSourceProfiles([]));
  }, []);

  const selectedRecord = useMemo(
    () => categories.find((item) => item.config.category_id === selectedCategory) ?? null,
    [categories, selectedCategory],
  );

  const sourceProfileId = connectionEnv.startsWith("SEARCH_SOURCE_")
    ? connectionEnv.slice("SEARCH_SOURCE_".length)
    : "";
  const sourceTables = useMemo(
    () => sourceCatalog?.tables.filter((item) => item.schema === schema) ?? [],
    [sourceCatalog, schema],
  );
  const selectedSourceTable = sourceTables.find((item) => item.name === table) ?? null;
  const sourceColumns = selectedSourceTable?.columns ?? [];

  const joinableRelationships = useMemo(() => {
    const relationships = sourceCatalog?.relationships || [];
    if (!sourceCatalog || !table) return [];
    const keyFor = (relationship: PlatformSourceRelationship) => [
      relationship.constraint_name,
      relationship.from_schema,
      relationship.from_table,
      relationship.to_schema,
      relationship.to_table,
    ].join("|");
    const selected = new Set(selectedJoinRelationships);
    const includedTables = new Set([schema + "." + table]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const relationship of relationships) {
        if (!selected.has(keyFor(relationship))) continue;
        const fromKey = relationship.from_schema + "." + relationship.from_table;
        const toKey = relationship.to_schema + "." + relationship.to_table;
        if (includedTables.has(fromKey) && !includedTables.has(toKey)) {
          includedTables.add(toKey);
          changed = true;
        }
      }
    }
    return relationships.filter((relationship) => {
      const key = keyFor(relationship);
      if (selected.has(key)) return true;
      const fromKey = relationship.from_schema + "." + relationship.from_table;
      const toKey = relationship.to_schema + "." + relationship.to_table;
      return includedTables.has(fromKey) && !includedTables.has(toKey);
    });
  }, [sourceCatalog, schema, table, selectedJoinRelationships]);

  const loadHistory = async (id: string) => {
    try {
      setCategoryHistory(await getPlatformCategoryHistory(id));
    } catch {
      setCategoryHistory([]);
    }
  };

  const loadFeedback = async (id: string) => {
    try {
      setFeedbackSummary(await getPlatformFeedback(id));
    } catch {
      setFeedbackSummary(null);
    }
  };

  const labelResult = async (documentId: string, isRelevant: boolean) => {
    if (!selectedCategory || !searchResult) return;
    const resultIndex = searchResult.data.findIndex((item) => item.id === documentId);
    if (resultIndex < 0) return;
    const result = searchResult.data[resultIndex];
    const resultRank = (searchResult.page - 1) * searchResult.page_size + resultIndex + 1;
    const action = documentId + (isRelevant ? ":relevant" : ":not-relevant");
    setFeedbackSaving(action);
    setError("");
    setNotice("");
    try {
      await recordPlatformFeedback({
        categoryId: selectedCategory,
        query: searchResult.query,
        documentId,
        isRelevant,
        resultRank,
        resultScore: result.score,
        configVersion: searchResult.config_version,
        indexVersion: searchResult.index_version,
      });
      setNotice(isRelevant ? "Marked result as relevant." : "Marked result as not relevant.");
      await loadFeedback(selectedCategory);
    } catch (e) {
      setError(getError(e, "Unable to save result feedback."));
    } finally {
      setFeedbackSaving("");
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
      filterable: false,
      data_type: "text",
      query_filter_mode: "none",
      query_match: "all",
    }]);
  };

  const removeField = (index: number) => {
    setFields((current) => current.filter((_, i) => i !== index));
  };

  const addSearchFilter = () => {
    const first = selectedRecord?.config.fields.find((field) => field.filterable);
    if (!first) {
      setError("Configure at least one filterable field on this engine before adding a search filter.");
      return;
    }
    setSearchFilters((current) => [...current, { field: first.key, operator: "eq", value: "" }]);
    setError("");
  };

  const updateSearchFilter = (index: number, patch: Partial<PlatformSearchFilter>) => {
    setSearchFilters((current) => current.map((item, i) => i === index ? { ...item, ...patch } : item));
  };

  const addSourceFilter = () => {
    // Never assume the source's soft-delete convention. Require the admin
    // to select a column and explicitly choose its intended filter.
    setSourceValidation(null);
    setSourceFilters((current) => [
      ...current,
      { column: "", operator: "eq", value: undefined, values: [] },
    ]);
  };

  const updateSourceFilter = (index: number, patch: Partial<PlatformSourceFilter>) => {
    setSourceValidation(null);
    setSourceFilters((current) => current.map((filter, i) => i === index ? { ...filter, ...patch } : filter));
  };

  const loadCategoryForEdit = (item: PlatformCategoryRecord) => {
    const config = item.config;
    setCategoryId(config.category_id);
    setDisplayName(config.display_name);
    setDescription(config.description || "");
    setSourceType(config.source.type);
    setSourceCatalog(null);
    setMappingSuggestions(null);
    setSourceValidation(null);
    setSelectedJoinRelationships([]);
    setViewPreview(null);
    if (config.source.type === "sqlserver") {
      setConnectionEnv(config.source.connection_env);
      setSchema(config.source.schema);
      setTable(config.source.table);
      setIdColumn(config.source.id_column);
      setMaxRecords(config.source.max_records);
      setSourceFilters(config.source.filters.map((filter) => ({ ...filter, values: [...(filter.values || [])] })));
    } else {
      setSourceFilters([]);
    }
    setFields(config.fields.map((field) => {
      const discreteField = ["skill", "location", "category"].includes(field.role);
      return {
        ...field,
        aliases: [...field.aliases],
        filterable: field.filterable ?? discreteField,
        data_type: field.data_type ?? (discreteField ? "keyword" : "text"),
        query_filter_mode: field.query_filter_mode ?? (discreteField ? "preferred" : "none"),
        query_match: field.query_match ?? "all",
      };
    }));
    setSynonymText((config.synonyms || []).map((group) => group.terms.join(", ")).join("\n"));
    setMinScore(config.rules.semantic_min_score);
    setHighConfidenceScore(config.rules.semantic_high_confidence_score ?? 75);
    setLexicalWeight(config.rules.lexical_weight);
    setSemanticWeight(config.rules.semantic_weight);
    setLexicalEnabled(config.rules.lexical_enabled);
    setSemanticEnabled(config.rules.semantic_enabled);
    setCandidateLimit(config.rules.semantic_candidate_limit);
    setPublicEnabled(config.public_search_enabled);
    setEditingVersion(item.config_version);
    setSelectedCategory(config.category_id);
    void loadHistory(config.category_id);
    void loadFeedback(config.category_id);
    setSearchFilters([]);
    setSearchResult(null);
    setNotice("Editing " + config.display_name + ". Save changes to create a new configuration version.");
    setError("");
  };

  const discoverSource = async () => {
    if (!sourceProfileId) {
      setError("Select a configured SQL connection profile first.");
      return;
    }
    setDiscoveringSource(true);
    setError("");
    setNotice("");
    try {
      const catalog = await discoverPlatformSourceSchema({ profileId: sourceProfileId });
      setSourceCatalog(catalog);
      setMappingSuggestions(null);
      setSelectedJoinRelationships([]);
      setViewPreview(null);
      setSourceValidation(null);

      const nextSchema = catalog.schemas.includes(schema) ? schema : (catalog.schemas[0] || "dbo");
      setViewSchema(nextSchema);
      const tables = catalog.tables.filter((item) => item.schema === nextSchema);
      const selected = tables.find((item) => item.name === table) ?? tables[0];
      setSchema(nextSchema);
      if (selected) {
        setTable(selected.name);
        const primaryKey = selected.primary_key_columns?.length === 1
          ? selected.primary_key_columns[0]
          : "";
        setIdColumn((current) => primaryKey || (selected.columns.some((column) => column.name === current)
          ? current
          : (selected.columns.find((column) => /id$/i.test(column.name))?.name || "")));
      } else {
        setTable("");
        setIdColumn("");
      }
      setNotice(
        "Connected to profile " + sourceProfileId + " (" + catalog.database + "). Discovered " +
        catalog.tables.length.toLocaleString() + " tables/views and " +
        (catalog.relationships?.length || 0).toLocaleString() + " declared foreign-key relationships."
      );
    } catch (e) {
      setSourceCatalog(null);
      setSourceValidation(null);
      setError(getError(e, "Unable to inspect the configured SQL source."));
    } finally {
      setDiscoveringSource(false);
    }
  };

  const relationshipKey = (relationship: PlatformSourceRelationship) => [
    relationship.constraint_name,
    relationship.from_schema,
    relationship.from_table,
    relationship.to_schema,
    relationship.to_table,
  ].join("|");

  const validateSource = async () => {
    if (!sourceProfileId || !schema || !table || !idColumn) return;
    setValidatingSource(true);
    setError("");
    setNotice("");
    try {
      const result = await validatePlatformSource({
        profileId: sourceProfileId,
        schema,
        table,
        idColumn,
        filters: sourceFilters,
        maxRecords,
      });
      setSourceValidation(result);
      setNotice(
        result.is_valid
          ? "Source validation passed. Document IDs are non-null and unique."
          : "Source validation found issues. Review the counts and warnings before syncing."
      );
    } catch (e) {
      setSourceValidation(null);
      setError(getError(e, "Unable to validate the selected document ID."));
    } finally {
      setValidatingSource(false);
    }
  };

  const previewJoinView = async () => {
    if (!sourceProfileId || !sourceCatalog || !idColumn) return;
    const relationships = (sourceCatalog.relationships || []).filter((relationship) =>
      selectedJoinRelationships.includes(relationshipKey(relationship))
    );
    setPreviewingView(true);
    setError("");
    setNotice("");
    try {
      const result = await previewPlatformJoinView({
        profileId: sourceProfileId,
        baseSchema: schema,
        baseTable: table,
        baseIdColumn: idColumn,
        viewSchema,
        viewName,
        selectedRelationships: relationships,
      });
      setViewPreview(result);
      setNotice(
        "Generated a SQL preview for " + result.view_schema + "." + result.view_name +
        " with " + result.joined_tables.length + " tables. No database changes were made."
      );
    } catch (e) {
      setViewPreview(null);
      setError(getError(e, "Unable to generate the SQL view preview."));
    } finally {
      setPreviewingView(false);
    }
  };

  const applyMappingSuggestions = async () => {
    if (!sourceProfileId || !schema || !table) return;
    setSuggestingMapping(true);
    setError("");
    setNotice("");
    try {
      const result = await suggestPlatformSourceMapping({
        profileId: sourceProfileId,
        schema,
        table,
      });
      setSourceValidation(null);
      const safeFields: PlatformFieldConfig[] = result.fields.map((field) => ({
        key: field.key,
        source_column: field.source_column,
        label: field.label,
        role: field.role,
        weight: field.weight,
        searchable: field.searchable,
        semantic: field.semantic,
        aliases: field.aliases,
        filterable: field.filterable ?? false,
        data_type: field.data_type ?? "text",
        query_filter_mode: field.query_filter_mode ?? "none",
        query_match: field.query_match ?? "all",
      }));
      setFields(safeFields);
      if (result.id_column_suggestion.column) {
        setIdColumn(result.id_column_suggestion.column);
      }
      setMappingSuggestions(result);
      const warnings = result.warnings.length ? " Review: " + result.warnings.join(" ") : "";
      setNotice(
        "Generated " + result.fields.length + " field suggestions for " + schema + "." + table +
        ". Identifier: " + (result.id_column_suggestion.column || "manual selection required") + "." + warnings
      );
    } catch (e) {
      setError(getError(e, "Unable to suggest field mappings."));
    } finally {
      setSuggestingMapping(false);
    }
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
              filters: sourceFilters,
            },
        fields,
        synonyms: parseSynonymGroups(synonymText),
        rules: {
          ...DEFAULT_RULES,
          lexical_enabled: lexicalEnabled,
          semantic_enabled: semanticEnabled,
          semantic_min_score: minScore,
          semantic_high_confidence_score: highConfidenceScore,
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
        ? "Engine created. Next, sync its data source or ingest documents."
        : "Engine configuration updated. Re-sync or re-ingest data to rebuild its index.");
      setEditingVersion(null);
      setChangeReason("");
      setSelectedCategory(saved.config.category_id);
      await loadHistory(saved.config.category_id);
      await loadFeedback(saved.config.category_id);
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
      const selectedFields = selectedRecord?.config.fields ?? [];
      const cleanFilters = searchFilters.map((item) => {
        const field = selectedFields.find((candidate) => candidate.key === item.field);
        if (!field?.filterable) throw new Error("Select a configured filterable field.");
        const operator = item.operator;
        if (operator === "in" || operator === "between") {
          const values = (item.values || []).map((value) => String(value).trim()).filter(Boolean);
          if (!values.length) throw new Error("Enter filter values for " + field.label + ".");
          if (operator === "between" && values.length !== 2) {
            throw new Error("Between filters require exactly two comma-separated values.");
          }
          return { field: item.field, operator, values };
        }
        if (operator === "is_null" || operator === "not_null") {
          return { field: item.field, operator };
        }
        if (item.value === undefined || item.value === null || String(item.value).trim() === "") {
          throw new Error("Enter a filter value for " + field.label + ".");
        }
        return { field: item.field, operator, value: item.value };
      });
      const result = await runPlatformAdminSearch({
        query,
        category: selectedCategory,
        filters: cleanFilters,
      });
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
          <div><span className="section-kicker">SEARCH ENGINE MANAGEMENT</span><h2>Search engines</h2></div>
          <button type="button" className="secondary-button" onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button>
        </div>
        <p className="panel-intro">Create a search engine with its own source, field mapping and ranking settings. The generic search pipeline runs separately from the existing Company and Product search.</p>
        {error && <div className="inline-error"><strong>Action failed</strong><span>{error}</span></div>}
        {notice && <div className="success-message">{notice}</div>}
        <div className="platform-category-list">
          {loading ? <p className="panel-intro">Loading categories…</p> : categories.length === 0 ? <p className="panel-intro">No search engines created yet.</p> : categories.map((item) => (
            <article className="platform-category-card" key={item.config.category_id}>
              <div>
                <strong>{item.config.display_name}</strong>
                <small>{item.config.category_id} · {item.config.source.type} · config v{item.config_version} · index v{item.index_version}</small>
                <small>{item.config.fields.length} mapped fields · {item.config.public_search_enabled ? "public search enabled" : "admin-only"}</small>
              </div>
              <div className="platform-category-actions">
                <button type="button" className="secondary-button" onClick={() => { setSelectedCategory(item.config.category_id); setSearchFilters([]); setSearchResult(null); void loadHistory(item.config.category_id); void loadFeedback(item.config.category_id); }}>Test</button>
                <button type="button" className="secondary-button" onClick={() => loadCategoryForEdit(item)}>Edit</button>
                {item.config.source.type === "sqlserver" && <button type="button" className="secondary-button" disabled={syncing === item.config.category_id} onClick={() => void syncCategory(item.config.category_id)}>{syncing === item.config.category_id ? "Syncing…" : "Sync source"}</button>}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel platform-form-panel">
        <div className="panel-title-row"><div><span className="section-kicker">01 / ENGINE SETUP</span><h2>{editingVersion === null ? "Create a search engine" : "Edit search engine"}</h2></div><span className="method-badge">Admin API</span></div>
        <form onSubmit={(event) => void createCategory(event)}>
          <div className="platform-form-grid">
            <label className="setting-field"><span>Engine ID</span><small>Lowercase identifier, e.g. people or articles.</small><input required value={categoryId} onChange={(e) => setCategoryId(e.target.value)} pattern="[a-z][a-z0-9_-]{1,62}" /></label>
            <label className="setting-field"><span>Display name</span><input required value={displayName} onChange={(e) => setDisplayName(e.target.value)} /></label>
            <label className="setting-field wide"><span>Description</span><input value={description} onChange={(e) => setDescription(e.target.value)} /></label>
          </div>

          <div className="settings-section-title"><div><h3>Data source</h3><p>Credentials stay on the server. The browser stores only the name of a server-side connection setting.</p></div></div>
          <div className="platform-form-grid">
            <label className="setting-field"><span>Source type</span><select value={sourceType} onChange={(e) => {
              setSourceType(e.target.value as "sqlserver" | "push");
              setSourceValidation(null);
            }}><option value="sqlserver">SQL Server table</option><option value="push">Application pushes records</option></select></label>
            {sourceType === "sqlserver" ? <>
              <label className="setting-field"><span>Reusable SQL connection</span>
                <select value={sourceProfileId} onChange={(e) => {
                  setConnectionEnv(e.target.value ? "SEARCH_SOURCE_" + e.target.value : "");
                  setSourceCatalog(null);
                  setSourceValidation(null);
                  setSourceFilters([]);
                  setMappingSuggestions(null);
                  setSelectedJoinRelationships([]);
                  setViewPreview(null);
                }} required>
                  <option value="">Select a configured connection</option>
                  {sourceProfiles.map((profile) => <option key={profile.profile_id} value={profile.profile_id}>{profile.profile_id}</option>)}
                  {sourceProfileId && !sourceProfiles.some((profile) => profile.profile_id === sourceProfileId) && <option value={sourceProfileId}>{sourceProfileId} (not currently discoverable)</option>}
                </select>
                <small>Connection credentials remain on the server. Profiles are reusable across search engines.</small>
              </label>
              <div className="setting-field">
                <span>Connection discovery</span>
                <button type="button" className="secondary-button" onClick={() => void discoverSource()} disabled={!sourceProfileId || discoveringSource}>{discoveringSource ? "Connecting…" : "Test connection & discover tables"}</button>
                {sourceProfiles.length === 0 && <small>No SQL connection profiles are registered. A platform operator must configure a server-side SEARCH_SOURCE_ profile once; never paste credentials here.</small>}
              </div>
              <label className="setting-field"><span>Schema</span>{sourceCatalog
                ? <select value={schema} onChange={(e) => {
                    setSchema(e.target.value);
                    setViewSchema(e.target.value);
                    setTable("");
                    setIdColumn("");
                    setSourceFilters([]);
                    setMappingSuggestions(null);
                    setSelectedJoinRelationships([]);
                    setViewPreview(null);
                    setSourceValidation(null);
                  }}>
                    {sourceCatalog.schemas.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                : <input value={schema} onChange={(e) => setSchema(e.target.value)} required />}</label>
              <label className="setting-field"><span>Table / view</span>{sourceCatalog
                ? <select value={table} onChange={(e) => {
                    const nextTable = sourceTables.find((item) => item.name === e.target.value);
                    setTable(e.target.value);
                    setSourceFilters([]);
                    setMappingSuggestions(null);
                    setSelectedJoinRelationships([]);
                    setViewPreview(null);
                    setSourceValidation(null);
                    setIdColumn(nextTable?.primary_key_columns?.length === 1
                      ? nextTable.primary_key_columns[0]
                      : (nextTable?.columns.find((column) => /id$/i.test(column.name))?.name || ""));
                  }} required>
                    <option value="">Select table or view</option>
                    {sourceTables.map((item) => <option key={item.name} value={item.name}>
                      {item.name} ({item.type === "VIEW" ? "view" : "table"})
                      {item.primary_key_columns?.length ? " · PK: " + item.primary_key_columns.join(", ") : ""}
                    </option>)}
                  </select>
                : <input value={table} onChange={(e) => setTable(e.target.value)} required />}</label>
              <label className="setting-field"><span>Unique ID column</span>{sourceCatalog
                ? <select value={idColumn} onChange={(e) => {
                    setIdColumn(e.target.value);
                    setSourceValidation(null);
                  }} required>
                    <option value="">Select unique ID column</option>
                    {sourceColumns.map((column) => <option key={column.name} value={column.name}>
                      {column.name} · {column.data_type}
                      {column.is_primary_key ? " · PK" : ""}
                      {column.is_foreign_key ? " · FK" : ""}
                    </option>)}
                  </select>
                : <input value={idColumn} onChange={(e) => setIdColumn(e.target.value)} required />}</label>
              <label className="setting-field"><span>Maximum records per sync</span><input type="number" min="1" max="100000" value={maxRecords} onChange={(e) => {
                setMaxRecords(Number(e.target.value));
                setSourceValidation(null);
              }} /></label>
              <div className="setting-field wide">
                <span>Source preflight</span>
                <button type="button" className="secondary-button" onClick={() => void validateSource()} disabled={!sourceCatalog || !table || !idColumn || validatingSource}>
                  {validatingSource ? "Validating source…" : "Validate ID uniqueness & filters"}
                </button>
                <small>Checks row count, NULL IDs and duplicate IDs using the selected filters. It does not read or return source records.</small>
              </div>
              {sourceValidation && <div className="mini-panel wide">
                <div>
                  <strong>{sourceValidation.is_valid ? "Document ID validation passed" : "Document ID validation needs attention"}</strong>
                  <p>
                    {sourceValidation.total_rows.toLocaleString()} rows ·
                    {" " + sourceValidation.non_null_ids.toLocaleString()} non-null IDs ·
                    {" " + sourceValidation.distinct_ids.toLocaleString()} distinct IDs
                  </p>
                  <p>
                    {sourceValidation.null_id_count.toLocaleString()} NULL IDs ·
                    {" " + sourceValidation.blank_id_count.toLocaleString()} blank IDs ·
                    {" " + sourceValidation.duplicate_id_count.toLocaleString()} duplicate IDs
                  </p>
                  {sourceValidation.warnings.map((warning) => <small key={warning}>{warning}</small>)}
                </div>
              </div>}
            </> : <p className="panel-intro wide">Create the engine, then select it below to push normalized records using the ingestion form.</p>}
          </div>

          {sourceType === "sqlserver" && sourceCatalog && selectedSourceTable && (sourceCatalog.relationships || []).length > 0 && <div className="platform-history">
            <div className="settings-section-title"><div><h3>Build a multi-table source view</h3><p>Select foreign-key relationships to include related parent-table fields. One-to-many joins are blocked to protect one document per base record.</p></div></div>
            <div className="platform-form-grid">
              <label className="setting-field"><span>View schema</span><input value={viewSchema} onChange={(e) => setViewSchema(e.target.value)} /></label>
              <label className="setting-field"><span>View name</span><input value={viewName} onChange={(e) => setViewName(e.target.value)} /></label>
            </div>
            {joinableRelationships.map((relationship) => {
              const key = relationshipKey(relationship);
              return <label className="platform-checkbox" key={key}>
                <input
                  type="checkbox"
                  checked={selectedJoinRelationships.includes(key)}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setSelectedJoinRelationships((current) => [...current, key]);
                    } else {
                      const remaining = new Set(selectedJoinRelationships.filter((item) => item !== key));
                      const reachable = new Set([schema + "." + table]);
                      const retained = new Set<string>();
                      let changed = true;
                      while (changed) {
                        changed = false;
                        for (const candidate of sourceCatalog?.relationships || []) {
                          const candidateKey = relationshipKey(candidate);
                          const fromKey = candidate.from_schema + "." + candidate.from_table;
                          const toKey = candidate.to_schema + "." + candidate.to_table;
                          if (remaining.has(candidateKey) && reachable.has(fromKey) && !reachable.has(toKey)) {
                            retained.add(candidateKey);
                            reachable.add(toKey);
                            changed = true;
                          }
                        }
                      }
                      setSelectedJoinRelationships(Array.from(retained));
                    }
                    setViewPreview(null);
                  }}
                />
                {relationship.from_schema}.{relationship.from_table}
                {" (" + relationship.columns.map((column) => column.from_column).join(", ") + ") → "}
                {relationship.to_schema}.{relationship.to_table}
                {" (" + relationship.columns.map((column) => column.to_column).join(", ") + ")"}
              </label>;
            })}
            <div className="settings-actions">
              <span className="action-spacer" />
              <button type="button" className="secondary-button" onClick={() => void previewJoinView()} disabled={previewingView || selectedJoinRelationships.length === 0 || !idColumn}>
                {previewingView ? "Generating preview…" : "Generate SQL view preview"}
              </button>
            </div>
            {viewPreview && <div className="mini-panel">
              <div>
                <strong>{viewPreview.view_schema}.{viewPreview.view_name}</strong>
                <p>Recommended Search AI source: {viewPreview.view_schema}.{viewPreview.view_name} · ID column: {viewPreview.document_id_column}</p>
                {viewPreview.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                <textarea className="platform-json-input" rows={14} readOnly value={viewPreview.view_sql} />
                <div className="settings-actions">
                  <span className="action-spacer" />
                  <button type="button" className="secondary-button" onClick={() => {
                    void navigator.clipboard.writeText(viewPreview.view_sql)
                      .then(() => setNotice("SQL preview copied to clipboard."))
                      .catch(() => setError("Clipboard access was unavailable. Select and copy the SQL manually."));
                  }}>Copy SQL</button>
                </div>
              </div>
            </div>}
          </div>}
          {sourceType === "sqlserver" && sourceCatalog && selectedSourceTable && <div className="platform-history">
            <div className="settings-section-title"><div><h3>Discovered table relationships</h3><p>Declared foreign keys only. These identify possible joins; no joins are executed automatically.</p></div></div>
            {(sourceCatalog.relationships || [])
              .filter((relationship) =>
                (relationship.from_schema === schema && relationship.from_table === table) ||
                (relationship.to_schema === schema && relationship.to_table === table)
              )
              .map((relationship) => <article className="platform-history-item" key={relationship.constraint_name + relationship.from_schema + relationship.from_table}>
                <strong>{relationship.from_schema}.{relationship.from_table} → {relationship.to_schema}.{relationship.to_table}</strong>
                <span>{relationship.columns.map((column) => column.from_column + " = " + column.to_column).join(", ")}</span>
                <small>{relationship.constraint_name}</small>
              </article>)}
            {(sourceCatalog.relationships || []).filter((relationship) =>
              (relationship.from_schema === schema && relationship.from_table === table) ||
              (relationship.to_schema === schema && relationship.to_table === table)
            ).length === 0 && <p className="panel-intro">No declared foreign-key relationships were found for this table or view.</p>}
          </div>}
          {sourceType === "sqlserver" && <div className="platform-source-filters">
            <div className="settings-section-title"><div><h3>Source filters</h3><p>Optional safe filters applied while reading the configured table. Do not use this to bypass record permissions.</p></div><button type="button" className="secondary-button" onClick={addSourceFilter}>Add filter</button></div>
            {sourceFilters.map((filter, index) => <div className="platform-filter-row" key={index}>
              <label className="setting-field"><span>Column</span>{sourceCatalog
                ? <select value={filter.column} required onChange={(e) => updateSourceFilter(index, { column: e.target.value })}>
                    <option value="">Select a source column</option>
                    {sourceColumns.map((column) => <option key={column.name} value={column.name}>
                      {column.name}
                      {column.is_primary_key ? " · PK" : ""}
                      {column.is_foreign_key ? " · FK" : ""}
                    </option>)}
                  </select>
                : <input value={filter.column} required onChange={(e) => updateSourceFilter(index, { column: e.target.value })} />}</label>
              <label className="setting-field"><span>Operator</span><select value={filter.operator} onChange={(e) => updateSourceFilter(index, { operator: e.target.value as PlatformSourceFilter["operator"], value: undefined, values: [] })}>
                <option value="eq">Equals</option><option value="ne">Not equal</option><option value="in">In list</option><option value="is_null">Is empty</option><option value="not_null">Is not empty</option>
              </select></label>
              {filter.operator === "eq" || filter.operator === "ne" ? <label className="setting-field"><span>Value</span><input required value={String(filter.value ?? "")} onChange={(e) => updateSourceFilter(index, { value: e.target.value })} /></label> : filter.operator === "in" ? <label className="setting-field"><span>Values (comma separated)</span><input required value={(filter.values || []).join(", ")} onChange={(e) => updateSourceFilter(index, { values: e.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} /></label> : <div className="setting-field"><span>Value</span><small>No value required</small></div>}
              <button type="button" className="secondary-button danger-button" onClick={() => {
                setSourceValidation(null);
                setSourceFilters((current) => current.filter((_, i) => i !== index));
              }}>Remove</button>
            </div>)}
          </div>}

          <div className="settings-section-title">
            <div><h3>Field mapping and weights</h3><p>Map source columns into standard searchable fields. Weights influence lexical and semantic relevance; automatic suggestions use metadata only and must be reviewed before saving.</p></div>
            <div className="settings-actions">
              {sourceType === "sqlserver" && <button type="button" className="secondary-button" onClick={() => void applyMappingSuggestions()} disabled={!sourceCatalog || !table || suggestingMapping}>
                {suggestingMapping ? "Suggesting…" : "Auto-suggest mappings"}
              </button>}
              <button type="button" className="secondary-button" onClick={addField}>Add field</button>
            </div>
          </div>
          {mappingSuggestions && <div className="mini-panel">
            <div>
              <strong>Mapping suggestion review</strong>
              <p>Suggested ID: {mappingSuggestions.id_column_suggestion.column || "Manual selection required"} · {mappingSuggestions.id_column_suggestion.reason}</p>
              {mappingSuggestions.warnings.map((warning) => <p key={warning}>{warning}</p>)}
              {mappingSuggestions.skipped_columns.length > 0 && <small>Excluded from automatic mapping: {mappingSuggestions.skipped_columns.map((column) => column.column).join(", ")}</small>}
              {mappingSuggestions.potential_filter_columns.length > 0 && <small>Possible status/filter columns (not applied): {mappingSuggestions.potential_filter_columns.join(", ")}</small>}
            </div>
          </div>}
          <div className="platform-fields">
            {fields.map((field, index) => <div className="platform-field-row" key={index}>
              <label className="setting-field"><span>Field key</span><input value={field.key} onChange={(e) => updateField(index, { key: e.target.value })} required /></label>
              <label className="setting-field"><span>Source column</span>{sourceCatalog
                ? <select value={field.source_column} onChange={(e) => updateField(index, { source_column: e.target.value })} required>
                    {!sourceColumns.some((column) => column.name === field.source_column) && <option value={field.source_column}>{field.source_column} (not in selected table)</option>}
                    {sourceColumns.map((column) => <option key={column.name} value={column.name}>{column.name} · {column.data_type}</option>)}
                  </select>
                : <input value={field.source_column} onChange={(e) => updateField(index, { source_column: e.target.value })} required />}</label>
              <label className="setting-field"><span>Label</span><input value={field.label} onChange={(e) => updateField(index, { label: e.target.value })} required /></label>
              <label className="setting-field"><span>Role</span><select value={field.role} onChange={(e) => updateField(index, { role: e.target.value as PlatformFieldRole })}>{ROLE_OPTIONS.map((role) => <option key={role} value={role}>{role}</option>)}</select></label>
              <label className="setting-field"><span>Weight</span><input type="number" min="0" max="100" step="0.5" value={field.weight} onChange={(e) => updateField(index, { weight: Number(e.target.value) })} /></label>
              <label className="setting-field"><span>Aliases (comma separated)</span><input value={field.aliases.join(", ")} onChange={(e) => updateField(index, { aliases: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></label>
              <label className="setting-field"><span>Data type</span><select value={field.data_type || "text"} onChange={(e) => updateField(index, { data_type: e.target.value as PlatformFieldConfig["data_type"] })}>
                <option value="text">Text</option><option value="keyword">Keyword / exact value</option><option value="integer">Integer</option><option value="number">Number</option><option value="boolean">Boolean</option><option value="date">Date / datetime</option>
              </select></label>
              <label className="platform-checkbox"><input type="checkbox" checked={field.searchable} onChange={(e) => updateField(index, { searchable: e.target.checked, ...(e.target.checked ? {} : { semantic: false }) })} /> Searchable</label>
              <label className="platform-checkbox"><input type="checkbox" checked={field.semantic} disabled={!field.searchable} onChange={(e) => updateField(index, { semantic: e.target.checked })} /> Include in semantic text</label>
              <label className="platform-checkbox"><input type="checkbox" checked={Boolean(field.filterable)} onChange={(e) => updateField(index, { filterable: e.target.checked, query_filter_mode: e.target.checked ? (field.query_filter_mode || "none") : "none" })} /> Allow result filters</label>
              <label className="setting-field"><span>Query match behavior</span><select value={field.query_filter_mode || "none"} disabled={!field.filterable} onChange={(e) => updateField(index, { query_filter_mode: e.target.value as PlatformFieldConfig["query_filter_mode"] })}>
                <option value="none">Ranking only</option><option value="preferred">Preferred (boost matches)</option><option value="required">Required (filter results)</option>
              </select></label>
              <label className="setting-field"><span>Multiple values</span><select value={field.query_match || "all"} disabled={!field.filterable || field.query_filter_mode !== "required"} onChange={(e) => updateField(index, { query_match: e.target.value as PlatformFieldConfig["query_match"] })}>
                <option value="all">Require all values</option><option value="any">Match any value</option>
              </select></label>
              <button type="button" className="secondary-button danger-button" onClick={() => removeField(index)} disabled={fields.length <= 1}>Remove</button>
            </div>)}
          </div>

          <div className="settings-section-title"><div><h3>Search synonyms</h3><p>Each line is one synonym group. Separate equivalent words or phrases with commas. Groups apply only to this engine.</p></div></div>
          <label className="setting-field wide">
            <span>Synonym groups</span>
            <textarea
              className="platform-json-input"
              rows={4}
              value={synonymText}
              onChange={(e) => setSynonymText(e.target.value)}
              placeholder={"developer, engineer, programmer\nmachine learning, ml, artificial intelligence\nproduct, item, goods"}
            />
            <small>Example: a query for “developer” can also retrieve “engineer”. Exact indexed names and values are protected from synonym expansion.</small>
          </label>

          <div className="settings-section-title"><div><h3>Search rules</h3><p>These are runtime ranking configurations, not model fine-tuning.</p></div></div>
          <div className="platform-form-grid">
            <label className="setting-field"><span>Minimum semantic score</span><input type="number" min="0" max="100" step="0.5" value={minScore} onChange={(e) => setMinScore(Number(e.target.value))} /></label>
            <label className="setting-field"><span>High-confidence semantic bypass</span><input type="number" min="0" max="100" step="0.5" value={highConfidenceScore} onChange={(e) => setHighConfidenceScore(Number(e.target.value))} /><small>Semantic matches below this score need at least one meaningful query word in common.</small></label>
            <label className="setting-field"><span>Semantic candidate limit</span><input type="number" min="1" max="200" value={candidateLimit} onChange={(e) => setCandidateLimit(Number(e.target.value))} /></label>
            <label className="setting-field"><span>Lexical weight</span><input type="number" min="0" max="1" step="0.05" value={lexicalWeight} onChange={(e) => setLexicalWeight(Number(e.target.value))} /></label>
            <label className="setting-field"><span>Semantic weight</span><input type="number" min="0" max="1" step="0.05" value={semanticWeight} onChange={(e) => setSemanticWeight(Number(e.target.value))} /></label>
            <label className="platform-checkbox"><input type="checkbox" checked={lexicalEnabled} onChange={(e) => setLexicalEnabled(e.target.checked)} /> Enable keyword matching</label>
            <label className="platform-checkbox"><input type="checkbox" checked={semanticEnabled} onChange={(e) => setSemanticEnabled(e.target.checked)} /> Enable semantic matching</label>
            <label className="platform-checkbox"><input type="checkbox" checked={publicEnabled} onChange={(e) => setPublicEnabled(e.target.checked)} /> Allow public search (only enable for approved non-sensitive data)</label>
            <label className="setting-field wide"><span>Change reason</span><input value={changeReason} onChange={(e) => setChangeReason(e.target.value)} maxLength={1000} placeholder="Why are you creating or changing this category?" /></label>
          </div>
          <div className="settings-actions"><span className="action-spacer" /><button type="submit" className="primary-button" disabled={saving || fields.length === 0 || (sourceType === "sqlserver" && !connectionEnv)}>{saving ? "Saving…" : editingVersion === null ? "Create search engine" : "Save engine changes"}</button>
            {editingVersion !== null && <button type="button" className="secondary-button" onClick={() => {
              setEditingVersion(null);
              setCategoryId("people");
              setDisplayName("People");
              setDescription("Search BONC people profiles");
              setSourceType("sqlserver");
              setConnectionEnv(sourceProfiles[0] ? "SEARCH_SOURCE_" + sourceProfiles[0].profile_id : "");
               setSourceCatalog(null);
              setSchema("dbo");
              setTable("Users");
              setIdColumn("UserId");
              setMaxRecords(50000);
              setSourceFilters([]);
              setFields(INITIAL_FIELDS);
              setSynonymText("");
              setMinScore(20);
              setHighConfidenceScore(75);
              setLexicalWeight(0.65);
              setSemanticWeight(0.35);
              setLexicalEnabled(true);
              setSemanticEnabled(true);
              setCandidateLimit(50);
              setPublicEnabled(false);
              setChangeReason("");
            }}>Cancel edit</button>}</div>
        </form>
      </section>

      <section className="panel platform-test-panel">
        <div className="panel-title-row"><div><span className="section-kicker">02 / INGESTION & TEST</span><h2>Load and test engine data</h2></div><span className="method-badge">Admin only</span></div>
        <div className="platform-form-grid">
          <label className="setting-field"><span>Search engine</span><select value={selectedCategory} onChange={(e) => { setSelectedCategory(e.target.value); setSearchFilters([]); setSearchResult(null); if (e.target.value) { void loadHistory(e.target.value); void loadFeedback(e.target.value); } else { setCategoryHistory([]); setFeedbackSummary(null); } }}><option value="">Select an engine</option>{categories.map((item) => <option key={item.config.category_id} value={item.config.category_id}>{item.config.display_name}</option>)}</select></label>
          <div className="setting-field"><span>Selected engine source</span><small>{selectedRecord ? selectedRecord.config.source.type + (selectedRecord.config.source.type === "sqlserver" ? " · " + selectedRecord.config.source.schema + "." + selectedRecord.config.source.table : "") : "No category selected"}</small></div>
        </div>
        {selectedRecord?.config.source.type === "push" && <form onSubmit={(event) => void ingest(event)}>
          <label className="setting-field"><span>Normalized documents (JSON array)</span><textarea className="platform-json-input" rows={9} value={rawDocuments} onChange={(e) => setRawDocuments(e.target.value)} spellCheck={false} /></label>
          <div className="settings-actions"><span className="action-spacer" /><button type="submit" className="primary-button" disabled={ingesting}>{ingesting ? "Ingesting…" : "Replace category documents"}</button></div>
        </form>}
        <form onSubmit={(event) => void runTestSearch(event)}>
          <div className="platform-form-grid">
            <label className="setting-field wide"><span>Natural-language test query</span><input value={query} onChange={(e) => setQuery(e.target.value)} required placeholder="e.g. Python backend developer in Noida" /></label>
          </div>
          {selectedRecord && <div className="settings-section-title">
            <div><h3>Optional result filters</h3><p>These filter indexed category results. They are different from SQL source filters used during sync.</p></div>
            <button type="button" className="secondary-button" onClick={addSearchFilter}>Add filter</button>
          </div>}
          {searchFilters.map((filter, index) => {
            const filterableFields = selectedRecord?.config.fields.filter((field) => field.filterable) ?? [];
            const selectedField = filterableFields.find((field) => field.key === filter.field) ?? filterableFields[0];
            const type = selectedField?.data_type || "text";
            const operators = type === "text" || type === "keyword"
              ? ["eq", "ne", "in", "contains", "is_null", "not_null"]
              : type === "boolean"
                ? ["eq", "ne", "in", "is_null", "not_null"]
                : ["eq", "ne", "in", "gte", "lte", "between", "is_null", "not_null"];
            return <div className="platform-field-row" key={index}>
              <label className="setting-field"><span>Field</span><select value={filter.field} onChange={(e) => updateSearchFilter(index, { field: e.target.value, operator: "eq", value: "", values: [] })}>
                {filterableFields.map((field) => <option key={field.key} value={field.key}>{field.label} ({field.key})</option>)}
              </select></label>
              <label className="setting-field"><span>Operator</span><select value={filter.operator} onChange={(e) => updateSearchFilter(index, { operator: e.target.value as PlatformSearchFilter["operator"], value: "", values: [] })}>
                {operators.map((operator) => <option key={operator} value={operator}>{operator}</option>)}
              </select></label>
              {filter.operator !== "is_null" && filter.operator !== "not_null" && <label className="setting-field"><span>{filter.operator === "between" ? "Lower, upper values" : filter.operator === "in" ? "Comma-separated values" : "Value"}</span><input
                value={filter.operator === "in" || filter.operator === "between" ? (filter.values || []).join(", ") : String(filter.value ?? "")}
                onChange={(e) => {
                  if (filter.operator === "in" || filter.operator === "between") {
                    updateSearchFilter(index, { values: e.target.value.split(",").map((value) => value.trim()).filter(Boolean) });
                  } else {
                    updateSearchFilter(index, { value: e.target.value });
                  }
                }}
                placeholder={type === "date" ? "YYYY-MM-DD" : "Enter a value"}
              /></label>}
              <button type="button" className="secondary-button danger-button" onClick={() => setSearchFilters((current) => current.filter((_, i) => i !== index))}>Remove</button>
            </div>;
          })}
          <div className="settings-actions"><span className="action-spacer" /><button type="submit" className="primary-button" disabled={!selectedCategory || searching}>{searching ? "Searching…" : "Test engine search"}</button></div>
        </form>
        {feedbackSummary && <div className="platform-history">
          <div className="settings-section-title"><div><h3>Relevance feedback</h3><p>Admin-labelled examples collected for future evaluation and model training.</p></div></div>
          <div className="platform-feedback-counts">
            <span>{feedbackSummary.total_count} total labels</span>
            <span>{feedbackSummary.relevant_count} relevant</span>
            <span>{feedbackSummary.not_relevant_count} not relevant</span>
          </div>
          {feedbackSummary.items.slice(0, 10).map((item) => <article className="platform-history-item" key={item.feedback_id}>
            <strong>{item.is_relevant ? "Relevant" : "Not relevant"}</strong>
            <span>{item.query}</span>
            <small>{item.document_id} · {item.created_by || "Unknown admin"}{item.result_rank ? " · rank #" + item.result_rank : ""}{item.config_version ? " · config v" + item.config_version : ""}{item.index_version !== null && item.index_version !== undefined ? " · index v" + item.index_version : ""}</small>
          </article>)}
        </div>}
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
          <div className="mini-panel"><div><strong>Query understanding & search plan</strong><p>{searchResult.query_plan.explanation}</p><p>{pretty(searchResult.query_plan)}</p></div></div>
          {searchResult.data.map((item) => <article className="platform-result-item" key={item.id}>
            <div><strong>{item.title}</strong><small>
              {item.exact_identifier_match
                ? "Exact document ID match"
                : (item.matched_fields.join(", ") || "Semantic match")}
              {item.phrase_score ? " · phrase " + item.phrase_score.toFixed(1) : ""}
            </small><pre>{pretty(item.fields)}</pre>
              <div className="platform-feedback-actions">
                <button type="button" className="secondary-button" disabled={feedbackSaving.startsWith(item.id)} onClick={() => void labelResult(item.id, true)}>{feedbackSaving === item.id + ":relevant" ? "Saving…" : "Relevant"}</button>
                <button type="button" className="secondary-button" disabled={feedbackSaving.startsWith(item.id)} onClick={() => void labelResult(item.id, false)}>{feedbackSaving === item.id + ":not-relevant" ? "Saving…" : "Not relevant"}</button>
              </div>
            </div>
            <strong>{item.score.toFixed(1)} pts</strong>
          </article>)}
          {searchResult.data.length === 0 && <p className="panel-intro">No matching documents in this category yet.</p>}
        </div>}
      </section>
    </div>
  );
}

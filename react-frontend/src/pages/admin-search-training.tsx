import { useState, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BrainCircuit,
  Database,
  Upload,
  Plus,
  Trash2,
  Play,
  RotateCcw,
  CheckCircle2,
  Building2,
  Package,
  Layers,
  ArrowRight,
  Sparkles,
  Terminal,
  Activity,
  FileSpreadsheet,
  Cpu,
  Search,
  KeyRound,
  LogOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";

// ─── Interfaces ─────────────────────────────────────────────────────────────
interface EntityItem {
  name: string;
  label: string;
  description: string;
  icon?: string;
  is_builtin: boolean;
  count: number;
  created_at?: string;
}

interface TrainingPair {
  id: string;
  query: string;
  target_text: string;
  entity_type: string;
  relevance: number;
  source: string;
  created_at: string;
}

interface RetrievalMetrics {
  recall_at_1: number;
  recall_at_5: number;
  mrr_at_10: number;
  num_queries: number;
  corpus_size: number;
}

interface Evaluation {
  split: "holdout" | "train";
  base: RetrievalMetrics | null;
  finetuned: RetrievalMetrics | null;
}

interface TrainingStatus {
  status: "idle" | "running" | "completed" | "failed";
  phase?: string;
  evaluation?: Evaluation | null;
  progress: number;
  current_epoch: number;
  total_epochs: number;
  current_loss: number;
  loss_history: { epoch: number; step: number; loss: number }[];
  logs: { timestamp: string; message: string; level: string }[];
  error?: string | null;
  active_model: string;
  last_trained_checkpoint?: string | null;
}

interface ModelCheckpoint {
  id: string;
  name: string;
  created_at: string;
  base_model: string;
  epochs: number;
  pair_count: number;
  entities_trained: string[];
  final_loss: number;
  negative_count?: number;
  evaluation?: Evaluation | null;
  is_active: boolean;
  is_base: boolean;
}

interface CompareRow {
  type: string;
  title: string;
  subtitle?: string;
  rank: number;
  score: number;
  active_rank?: number;
  rank_change?: number;
}

interface Comparison {
  candidate_checkpoint_id: string;
  pool_size: number;
  active: CompareRow[];
  candidate: CompareRow[];
  note: string;
}

// ─── Admin Key (sent as X-Admin-Key on every admin request) ─────────────────
const ADMIN_KEY_STORAGE = "bonc_admin_key";
const ADMIN_AUTH_EVENT = "bonc-admin-unauthorized";

function readStoredKey(): string {
  try {
    return localStorage.getItem(ADMIN_KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

function writeStoredKey(key: string) {
  try {
    if (key) localStorage.setItem(ADMIN_KEY_STORAGE, key);
    else localStorage.removeItem(ADMIN_KEY_STORAGE);
  } catch {
    /* storage unavailable — key lives in memory only */
  }
}

let currentAdminKey = readStoredKey();

// ─── API Helpers ────────────────────────────────────────────────────────────
async function adminFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (currentAdminKey) headers.set("X-Admin-Key", currentAdminKey);
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Request failed (${res.status})` }));
    if (res.status === 401 || res.status === 503) {
      window.dispatchEvent(new CustomEvent(ADMIN_AUTH_EVENT, { detail: err.detail }));
    }
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res;
}

async function apiGet<T>(url: string): Promise<T> {
  const res = await adminFetch(url);
  const data = await res.json();
  return data.data;
}

async function apiPost<T>(url: string, body?: any): Promise<T> {
  const res = await adminFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
}

async function apiPostForm<T>(url: string, form: FormData): Promise<T> {
  const res = await adminFetch(url, { method: "POST", body: form });
  return res.json();
}

async function apiDelete<T>(url: string): Promise<T> {
  const res = await adminFetch(url, { method: "DELETE" });
  return res.json();
}

const pct = (v?: number | null) => (v == null ? "—" : `${(v * 100).toFixed(0)}%`);

function MetricDelta({ base, tuned }: { base?: number | null; tuned?: number | null }) {
  if (base == null || tuned == null) return <span className="font-mono">{pct(tuned ?? base)}</span>;
  const diff = (tuned - base) * 100;
  const color = diff > 0.5 ? "text-emerald-600" : diff < -0.5 ? "text-destructive" : "text-muted-foreground";
  return (
    <span className="font-mono whitespace-nowrap">
      {pct(base)} → {pct(tuned)}{" "}
      <span className={color}>
        ({diff >= 0 ? "+" : ""}
        {diff.toFixed(0)})
      </span>
    </span>
  );
}

export default function AdminSearchTrainingPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState("entities");

  // State: Admin key gate
  const [adminKey, setAdminKey] = useState(currentAdminKey);
  const [keyInput, setKeyInput] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const hasKey = !!adminKey;

  useEffect(() => {
    const onUnauthorized = (e: Event) => {
      currentAdminKey = "";
      writeStoredKey("");
      setAdminKey("");
      setAuthError((e as CustomEvent).detail || "Admin key rejected.");
      queryClient.removeQueries({ queryKey: ["admin"] });
    };
    window.addEventListener(ADMIN_AUTH_EVENT, onUnauthorized);
    return () => window.removeEventListener(ADMIN_AUTH_EVENT, onUnauthorized);
  }, [queryClient]);

  const submitAdminKey = () => {
    const key = keyInput.trim();
    if (!key) return;
    currentAdminKey = key;
    writeStoredKey(key);
    setAdminKey(key);
    setAuthError(null);
    setKeyInput("");
  };

  const signOutAdmin = () => {
    currentAdminKey = "";
    writeStoredKey("");
    setAdminKey("");
    queryClient.removeQueries({ queryKey: ["admin"] });
  };

  // State: Training Data Tab
  const [entityFilter, setEntityFilter] = useState("all");
  const [queryFilter, setQueryFilter] = useState("");
  const [page, setPage] = useState(1);
  const [isAddPairOpen, setIsAddPairOpen] = useState(false);
  const [isUploadPairsOpen, setIsUploadPairsOpen] = useState(false);
  const [newPairQuery, setNewPairQuery] = useState("");
  const [newPairTarget, setNewPairTarget] = useState("");
  const [newPairEntity, setNewPairEntity] = useState("product");
  const [newPairRelevance, setNewPairRelevance] = useState("1");
  const [uploadFile, setUploadFile] = useState<File | null>(null);

  // State: Dynamic Entity Tab
  const [isAddEntityOpen, setIsAddEntityOpen] = useState(false);
  const [newEntityName, setNewEntityName] = useState("");
  const [newEntityLabel, setNewEntityLabel] = useState("");
  const [newEntityDesc, setNewEntityDesc] = useState("");
  const [entityFile, setEntityFile] = useState<File | null>(null);

  // State: Retraining Console Tab
  const [trainEpochs, setTrainEpochs] = useState("3");
  const [trainBatchSize, setTrainBatchSize] = useState("16");
  const [trainLR, setTrainLR] = useState("0.00002");
  const [selectedEntities, setSelectedEntities] = useState<string[]>(["all"]);
  const logTerminalRef = useRef<HTMLDivElement>(null);

  // State: Test Sandbox
  const [sandboxQuery, setSandboxQuery] = useState("Steel Hinges");
  const [sandboxResults, setSandboxResults] = useState<any[]>([]);
  const [sandboxCandidate, setSandboxCandidate] = useState("none");
  const [sandboxComparison, setSandboxComparison] = useState<Comparison | null>(null);
  const [isTestingQuery, setIsTestingQuery] = useState(false);

  // ─── Queries ──────────────────────────────────────────────────────────────
  const { data: entities = [] } = useQuery<EntityItem[]>({
    queryKey: ["admin", "entities"],
    queryFn: () => apiGet("/api/admin/entities"),
    enabled: hasKey,
  });

  const { data: pairsData } = useQuery<{
    items: TrainingPair[];
    total_count: number;
    page: number;
    page_size: number;
  }>({
    queryKey: ["admin", "training-pairs", entityFilter, queryFilter, page],
    queryFn: () =>
      apiGet(
        `/api/admin/training-pairs?entity_type=${entityFilter}&q=${encodeURIComponent(
          queryFilter
        )}&page=${page}&page_size=15`
      ),
    enabled: hasKey,
  });

  const { data: stats } = useQuery<any>({
    queryKey: ["admin", "training-stats"],
    queryFn: () => apiGet("/api/admin/training-pairs/stats"),
    enabled: hasKey,
  });

  const { data: checkpoints = [] } = useQuery<ModelCheckpoint[]>({
    queryKey: ["admin", "models"],
    queryFn: () => apiGet("/api/admin/models"),
    enabled: hasKey,
  });

  const { data: trainStatus } = useQuery<TrainingStatus>({
    queryKey: ["admin", "train-status"],
    queryFn: () => apiGet("/api/admin/train/status"),
    enabled: hasKey,
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      return s === "running" ? 1500 : 5000;
    },
  });

  // When a run finishes, refresh the checkpoint list so the new one appears.
  const prevTrainStatus = useRef<string | undefined>(undefined);
  useEffect(() => {
    const s = trainStatus?.status;
    if (prevTrainStatus.current === "running" && s && s !== "running") {
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
    }
    prevTrainStatus.current = s;
  }, [trainStatus?.status, queryClient]);

  // Dynamic entity names available
  const availableEntityNames = ["all", ...new Set(entities.map((e) => e.name))];

  // Auto-scroll logs
  useEffect(() => {
    if (logTerminalRef.current) {
      logTerminalRef.current.scrollTop = logTerminalRef.current.scrollHeight;
    }
  }, [trainStatus?.logs]);

  // ─── Mutations ────────────────────────────────────────────────────────────
  const addPairMutation = useMutation({
    mutationFn: () =>
      apiPost("/api/admin/training-pairs", {
        query: newPairQuery,
        target_text: newPairTarget,
        entity_type: newPairEntity,
        relevance: Number(newPairRelevance),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "training-pairs"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "training-stats"] });
      setIsAddPairOpen(false);
      setNewPairQuery("");
      setNewPairTarget("");
      toast({
        title: "Training Pair Added",
        description:
          Number(newPairRelevance) >= 0.5
            ? "Positive pair ready for fine-tuning."
            : "Hard negative saved — the model will learn to rank this target lower.",
      });
    },
    onError: (err: any) => {
      toast({ title: "Error", description: err.message, variant: "destructive" });
    },
  });

  const deletePairMutation = useMutation({
    mutationFn: (id: string) => apiDelete(`/api/admin/training-pairs/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "training-pairs"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "training-stats"] });
      toast({ title: "Pair Deleted" });
    },
  });

  const uploadPairsMutation = useMutation({
    mutationFn: async () => {
      if (!uploadFile) throw new Error("Please select a CSV or JSON file");
      const formData = new FormData();
      formData.append("file", uploadFile);
      formData.append("default_entity_type", newPairEntity);
      return apiPostForm<any>("/api/admin/training-pairs/upload", formData);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "training-pairs"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "training-stats"] });
      setIsUploadPairsOpen(false);
      setUploadFile(null);
      const r = data.data;
      const extras = [
        r.duplicate_count ? `${r.duplicate_count} duplicates skipped` : "",
        r.skipped_count ? `${r.skipped_count} invalid rows skipped` : "",
      ].filter(Boolean);
      toast({
        title: "Dataset Imported",
        description:
          `Imported ${r.imported_count} pairs.` +
          (extras.length ? ` ${extras.join(", ")}.` : "") +
          (r.errors?.length ? ` First issue: ${r.errors[0]}` : ""),
      });
    },
    onError: (err: any) => {
      toast({ title: "Upload Failed", description: err.message, variant: "destructive" });
    },
  });

  const registerEntityMutation = useMutation({
    mutationFn: async () => {
      const formData = new FormData();
      formData.append("name", newEntityName);
      formData.append("label", newEntityLabel);
      formData.append("description", newEntityDesc);
      if (entityFile) formData.append("file", entityFile);
      return apiPostForm<any>("/api/admin/entities", formData);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "entities"] });
      setIsAddEntityOpen(false);
      setNewEntityName("");
      setNewEntityLabel("");
      setNewEntityDesc("");
      setEntityFile(null);
      toast({
        title: "Entity Registered",
        description: `Entity '${data.data.label}' initialized with ${data.data.record_count} items.`,
      });
    },
    onError: (err: any) => {
      toast({ title: "Error", description: err.message, variant: "destructive" });
    },
  });

  const deleteEntityMutation = useMutation({
    mutationFn: (name: string) => apiDelete(`/api/admin/entities/${name}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "entities"] });
      toast({ title: "Entity Removed" });
    },
  });

  const startTrainMutation = useMutation({
    mutationFn: () =>
      apiPost("/api/admin/train", {
        epochs: Number(trainEpochs),
        batch_size: Number(trainBatchSize),
        learning_rate: Number(trainLR),
        entities: selectedEntities,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "train-status"] });
      toast({ title: "Retraining Started", description: "Background fine-tuning is underway." });
    },
    onError: (err: any) => {
      toast({ title: "Failed to Start Training", description: err.message, variant: "destructive" });
    },
  });

  const activateModelMutation = useMutation({
    mutationFn: (ckptId: string) =>
      apiPost("/api/admin/models/activate", { checkpoint_id: ckptId }),
    onSuccess: (data: any) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "train-status"] });
      toast({
        title: "Model Activated & Hot-Swapped",
        description: data.message || "New model is now serving live search requests with zero downtime.",
      });
    },
    onError: (err: any) => {
      toast({ title: "Activation Failed", description: err.message, variant: "destructive" });
    },
  });

  const handleTestQuery = async () => {
    if (!sandboxQuery.trim()) return;
    setIsTestingQuery(true);
    try {
      const res = await apiPost<any>("/api/admin/test-query", {
        query: sandboxQuery.trim(),
        entity_type: "all",
        candidate_checkpoint_id: sandboxCandidate === "none" ? null : sandboxCandidate,
      });
      setSandboxResults(res.active_model_results || []);
      setSandboxComparison(res.comparison || null);
    } catch (err: any) {
      toast({ title: "Test Query Failed", description: err.message, variant: "destructive" });
    } finally {
      setIsTestingQuery(false);
    }
  };

  const isTraining = trainStatus?.status === "running";
  const activeCheckpoint = checkpoints.find((c) => c.is_active);

  if (!hasKey) {
    return (
      <div className="container py-16 max-w-md mx-auto px-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <KeyRound className="h-5 w-5 text-primary" /> Admin Access Required
            </CardTitle>
            <CardDescription className="text-xs">
              Enter the admin key (the <code className="bg-muted px-1 rounded">ADMIN_API_KEY</code> value in{" "}
              <code className="bg-muted px-1 rounded">py-backend/.env</code>). It is stored in this browser only.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {authError && <p className="text-xs text-destructive">{authError}</p>}
            <Input
              type="password"
              placeholder="Admin key"
              value={keyInput}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submitAdminKey()}
              autoFocus
            />
            <Button className="w-full" onClick={submitAdminKey} disabled={!keyInput.trim()}>
              Unlock Admin Console
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container py-8 max-w-7xl mx-auto space-y-6 animate-in fade-in duration-500">
      {/* Top Banner Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-bold tracking-tight">Semantic Search Admin & Retraining</h1>
            <Badge variant="outline" className="gap-1 border-primary/40 text-primary">
              <Sparkles className="h-3 w-3" /> Zero-Code Control
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1">
            Register new entity domains, curate training pairs, and fine-tune models with zero-downtime hot-swapping.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <span className="text-xs text-muted-foreground block">Active Model</span>
            <span className="font-mono text-xs font-semibold px-2.5 py-1 bg-secondary rounded-md inline-block">
              {trainStatus?.active_model ? trainStatus.active_model.split("/").pop() : "all-MiniLM-L6-v2"}
            </span>
          </div>

          {isTraining && (
            <Badge variant="default" className="bg-amber-600 hover:bg-amber-700 animate-pulse gap-1.5 py-1.5">
              <Activity className="h-3.5 w-3.5 animate-spin" /> Training in Progress ({trainStatus?.progress}%)
            </Badge>
          )}

          <Button variant="ghost" size="sm" className="gap-1.5 text-xs" onClick={signOutAdmin}>
            <LogOut className="h-3.5 w-3.5" /> Lock
          </Button>
        </div>
      </div>

      {/* Main Tabs Navigation */}
      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
        <TabsList className="grid grid-cols-4 w-full max-w-2xl">
          <TabsTrigger value="entities" className="gap-2">
            <Layers className="h-4 w-4" /> Entities
          </TabsTrigger>
          <TabsTrigger value="dataset" className="gap-2">
            <Database className="h-4 w-4" /> Training Data
          </TabsTrigger>
          <TabsTrigger value="training" className="gap-2">
            <Cpu className="h-4 w-4" /> Retraining Console
          </TabsTrigger>
          <TabsTrigger value="models" className="gap-2">
            <BrainCircuit className="h-4 w-4" /> Models & Sandbox
          </TabsTrigger>
        </TabsList>

        {/* ═════════════════════════════════════════════════════════════════════
            TAB 1: DYNAMIC ENTITIES MANAGER
        ═════════════════════════════════════════════════════════════════════ */}
        <TabsContent value="entities" className="space-y-6">
          <div className="flex justify-between items-center">
            <div>
              <h2 className="text-xl font-bold tracking-tight">Searchable Entities</h2>
              <p className="text-sm text-muted-foreground">
                All entities indexed in memory. The system handles vector embeddings and keyword indexing automatically.
              </p>
            </div>

            <Dialog open={isAddEntityOpen} onOpenChange={setIsAddEntityOpen}>
              <DialogTrigger asChild>
                <Button className="gap-2">
                  <Plus className="h-4 w-4" /> Register New Entity
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-lg">
                <DialogHeader>
                  <DialogTitle>Register New Searchable Entity</DialogTitle>
                  <DialogDescription>
                    Add a new category (e.g. 'people', 'services', 'machinery'). Upload an initial CSV or JSON directory.
                  </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                  <div>
                    <label className="text-xs font-semibold mb-1 block">Entity Identifier (slug)</label>
                    <Input
                      placeholder="e.g. people or services"
                      value={newEntityName}
                      onChange={(e) => setNewEntityName(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-semibold mb-1 block">Display Label</label>
                    <Input
                      placeholder="e.g. People & Professionals"
                      value={newEntityLabel}
                      onChange={(e) => setNewEntityLabel(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-semibold mb-1 block">Description</label>
                    <Input
                      placeholder="Brief summary of what this entity represents"
                      value={newEntityDesc}
                      onChange={(e) => setNewEntityDesc(e.target.value)}
                    />
                  </div>
                  <div>
                    <label className="text-xs font-semibold mb-1 block">Initial Dataset (CSV or JSON, optional)</label>
                    <Input
                      type="file"
                      accept=".csv,.json"
                      onChange={(e) => setEntityFile(e.target.files?.[0] || null)}
                    />
                  </div>
                </div>

                <DialogFooter>
                  <Button variant="outline" onClick={() => setIsAddEntityOpen(false)}>
                    Cancel
                  </Button>
                  <Button
                    onClick={() => registerEntityMutation.mutate()}
                    disabled={!newEntityName || !newEntityLabel || registerEntityMutation.isPending}
                  >
                    {registerEntityMutation.isPending ? "Creating Index..." : "Create & Index Entity"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>

          {/* Entity Cards Grid */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {entities.map((ent) => {
              const Icon =
                ent.name === "business"
                  ? Building2
                  : ent.name === "product"
                  ? Package
                  : Layers;

              return (
                <Card key={ent.name} className="relative overflow-hidden hover:shadow-md transition-shadow">
                  <CardHeader className="pb-3 flex flex-row items-start justify-between gap-2">
                    <div className="flex items-center gap-3">
                      <div className="h-10 w-10 rounded-lg bg-primary/10 text-primary flex items-center justify-center">
                        <Icon className="h-5 w-5" />
                      </div>
                      <div>
                        <CardTitle className="text-base">{ent.label}</CardTitle>
                        <span className="font-mono text-xs text-muted-foreground">{ent.name}</span>
                      </div>
                    </div>
                    {ent.is_builtin ? (
                      <Badge variant="secondary" className="text-[10px]">
                        Built-in
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-[10px] text-emerald-600 border-emerald-500/40">
                        Dynamic
                      </Badge>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <p className="text-xs text-muted-foreground line-clamp-2 min-h-[32px]">
                      {ent.description || "Active searchable entity index."}
                    </p>

                    <div className="flex items-center justify-between text-xs pt-2 border-t">
                      <span className="text-muted-foreground">Indexed Records:</span>
                      <span className="font-bold font-mono text-sm">{ent.count.toLocaleString()}</span>
                    </div>

                    {!ent.is_builtin && (
                      <div className="pt-1 flex justify-end">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:bg-destructive/10 text-xs h-7 gap-1"
                          onClick={() => deleteEntityMutation.mutate(ent.name)}
                        >
                          <Trash2 className="h-3 w-3" /> Remove Entity
                        </Button>
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        {/* ═════════════════════════════════════════════════════════════════════
            TAB 2: TRAINING DATASET HUB
        ═════════════════════════════════════════════════════════════════════ */}
        <TabsContent value="dataset" className="space-y-6">
          {/* Stats Bar */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Card className="p-4">
              <span className="text-xs text-muted-foreground">Total Training Pairs</span>
              <p className="text-2xl font-bold font-mono mt-1">{stats?.total_pairs || 0}</p>
              <span className="text-[11px] text-muted-foreground">
                {stats?.positive_pairs || 0} positive · {stats?.negative_pairs || 0} hard negative
              </span>
            </Card>
            {Object.entries(stats?.by_entity || {}).map(([etype, cnt]: any) => (
              <Card key={etype} className="p-4">
                <span className="text-xs text-muted-foreground capitalize">{etype} Pairs</span>
                <p className="text-2xl font-bold font-mono mt-1 text-primary">{cnt}</p>
              </Card>
            ))}
          </div>

          {/* Action Bar */}
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-muted-foreground">Entity Filter:</span>
              {availableEntityNames.map((type) => (
                <Badge
                  key={type}
                  variant={entityFilter === type ? "default" : "outline"}
                  className="cursor-pointer capitalize text-xs"
                  onClick={() => {
                    setEntityFilter(type);
                    setPage(1);
                  }}
                >
                  {type}
                </Badge>
              ))}

              <div className="relative w-56 ml-2">
                <Search className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Filter query/match..."
                  value={queryFilter}
                  onChange={(e) => {
                    setQueryFilter(e.target.value);
                    setPage(1);
                  }}
                  className="h-8 pl-8 text-xs"
                />
              </div>
            </div>

            <div className="flex items-center gap-2">
              {/* Add Single Pair Modal */}
              <Dialog open={isAddPairOpen} onOpenChange={setIsAddPairOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" variant="outline" className="gap-1.5 text-xs">
                    <Plus className="h-3.5 w-3.5" /> Add Training Pair
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Add Search Training Pair</DialogTitle>
                    <DialogDescription>
                      Maps an anticipated user query to a desired product, business, or entity match.
                    </DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 py-2">
                    <div>
                      <label className="text-xs font-semibold mb-1 block">User Search Query</label>
                      <Input
                        placeholder="e.g. 'industrial valve manufacturer' or 'brass fittings'"
                        value={newPairQuery}
                        onChange={(e) => setNewPairQuery(e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="text-xs font-semibold mb-1 block">Target Document / Searchable Text</label>
                      <Input
                        placeholder="e.g. 'Apex Valves Mumbai SS Ball Valves'"
                        value={newPairTarget}
                        onChange={(e) => setNewPairTarget(e.target.value)}
                      />
                    </div>
                    <div>
                      <label className="text-xs font-semibold mb-1 block">Entity Type</label>
                      <Select value={newPairEntity} onValueChange={setNewPairEntity}>
                        <SelectTrigger>
                          <SelectValue placeholder="Select type" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="product">Product</SelectItem>
                          <SelectItem value="business">Business</SelectItem>
                          {entities
                            .filter((e) => !["product", "business"].includes(e.name))
                            .map((e) => (
                              <SelectItem key={e.name} value={e.name}>
                                {e.label}
                              </SelectItem>
                            ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <label className="text-xs font-semibold mb-1 block">Relationship</label>
                      <Select value={newPairRelevance} onValueChange={setNewPairRelevance}>
                        <SelectTrigger>
                          <SelectValue placeholder="Relationship" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="1">Relevant — query should find this target</SelectItem>
                          <SelectItem value="0">Not relevant — hard negative (rank lower)</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setIsAddPairOpen(false)}>
                      Cancel
                    </Button>
                    <Button
                      onClick={() => addPairMutation.mutate()}
                      disabled={!newPairQuery || !newPairTarget || addPairMutation.isPending}
                    >
                      Save Pair
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>

              {/* Upload Dataset Modal */}
              <Dialog open={isUploadPairsOpen} onOpenChange={setIsUploadPairsOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" className="gap-1.5 text-xs">
                    <Upload className="h-3.5 w-3.5" /> Upload Dataset (CSV/XLSX)
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Bulk Upload Training Dataset</DialogTitle>
                    <DialogDescription>
                      Upload a CSV, Excel (.xlsx), or JSON file containing search queries and target texts.
                    </DialogDescription>
                  </DialogHeader>
                  <div className="space-y-4 py-2">
                    <div className="border-2 border-dashed rounded-lg p-6 text-center space-y-2">
                      <FileSpreadsheet className="h-10 w-10 mx-auto text-muted-foreground opacity-60" />
                      <Input
                        type="file"
                        accept=".csv,.xlsx,.json"
                        onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                      />
                      <p className="text-xs text-muted-foreground">
                        Columns supported: <code className="bg-muted px-1 rounded">query</code>,{" "}
                        <code className="bg-muted px-1 rounded">target_text</code>,{" "}
                        <code className="bg-muted px-1 rounded">entity_type</code>,{" "}
                        <code className="bg-muted px-1 rounded">relevance</code> (0–1; below 0.5 = hard negative).
                        Duplicates and invalid rows are skipped.
                      </p>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setIsUploadPairsOpen(false)}>
                      Cancel
                    </Button>
                    <Button
                      onClick={() => uploadPairsMutation.mutate()}
                      disabled={!uploadFile || uploadPairsMutation.isPending}
                    >
                      {uploadPairsMutation.isPending ? "Uploading & Ingesting..." : "Upload Pairs"}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </div>

          {/* Pairs Table */}
          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b bg-muted/40 text-muted-foreground text-left">
                    <th className="py-2.5 px-4 font-semibold">Search Query</th>
                    <th className="py-2.5 px-4 font-semibold">Target Match Text</th>
                    <th className="py-2.5 px-4 font-semibold">Entity Type</th>
                    <th className="py-2.5 px-4 font-semibold">Label</th>
                    <th className="py-2.5 px-4 font-semibold">Source</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {pairsData?.items?.length ? (
                    pairsData.items.map((pair) => (
                      <tr key={pair.id} className="hover:bg-muted/20 transition-colors">
                        <td className="py-2.5 px-4 font-medium text-foreground max-w-[220px] truncate">
                          "{pair.query}"
                        </td>
                        <td className="py-2.5 px-4 text-muted-foreground max-w-[320px] truncate">
                          {pair.target_text}
                        </td>
                        <td className="py-2.5 px-4">
                          <Badge variant="outline" className="capitalize text-[10px]">
                            {pair.entity_type}
                          </Badge>
                        </td>
                        <td className="py-2.5 px-4">
                          {(pair.relevance ?? 1) >= 0.5 ? (
                            <Badge variant="secondary" className="text-[10px]">Positive</Badge>
                          ) : (
                            <Badge variant="outline" className="text-[10px] text-destructive border-destructive/40">
                              Negative
                            </Badge>
                          )}
                        </td>
                        <td className="py-2.5 px-4 font-mono text-[10px] text-muted-foreground">
                          {pair.source}
                        </td>
                        <td className="py-2.5 px-4 text-right">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-6 w-6 text-muted-foreground hover:text-destructive"
                            onClick={() => deletePairMutation.mutate(pair.id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="py-8 text-center text-muted-foreground">
                        No training pairs found. Upload a dataset or add training pairs above.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Pagination Controls */}
            {pairsData && pairsData.total_count > 15 && (
              <div className="flex items-center justify-between p-3 border-t text-xs">
                <span className="text-muted-foreground">
                  Showing {(page - 1) * 15 + 1}–{Math.min(page * 15, pairsData.total_count)} of {pairsData.total_count}
                </span>
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs"
                    disabled={page <= 1}
                    onClick={() => setPage(page - 1)}
                  >
                    Previous
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-xs"
                    disabled={page * 15 >= pairsData.total_count}
                    onClick={() => setPage(page + 1)}
                  >
                    Next
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </TabsContent>

        {/* ═════════════════════════════════════════════════════════════════════
            TAB 3: RETRAINING CONSOLE
        ═════════════════════════════════════════════════════════════════════ */}
        <TabsContent value="training" className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Hyperparameter Form */}
            <Card className="lg:col-span-1">
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Cpu className="h-4 w-4 text-primary" /> Training Configuration
                </CardTitle>
                <CardDescription className="text-xs">
                  Fine-tunes the SentenceTransformer with Multiple Negatives Ranking Loss, using hard negatives
                  where available, then scores the model on held-out queries.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div>
                  <label className="text-xs font-semibold mb-1 block">Epochs (Iterations)</label>
                  <Select value={trainEpochs} onValueChange={setTrainEpochs} disabled={isTraining}>
                    <SelectTrigger>
                      <SelectValue placeholder="Epochs" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="1">1 Epoch (Quick Test)</SelectItem>
                      <SelectItem value="3">3 Epochs (Standard - Recommended)</SelectItem>
                      <SelectItem value="5">5 Epochs (Deep Adaptation)</SelectItem>
                      <SelectItem value="10">10 Epochs (Extensive)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <label className="text-xs font-semibold mb-1 block">Batch Size</label>
                  <Select value={trainBatchSize} onValueChange={setTrainBatchSize} disabled={isTraining}>
                    <SelectTrigger>
                      <SelectValue placeholder="Batch size" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="8">8 (Low Memory)</SelectItem>
                      <SelectItem value="16">16 (Recommended for CPU/GPU)</SelectItem>
                      <SelectItem value="32">32 (Faster GPU)</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div>
                  <label className="text-xs font-semibold mb-1 block">Learning Rate</label>
                  <Input
                    value={trainLR}
                    onChange={(e) => setTrainLR(e.target.value)}
                    disabled={isTraining}
                    className="font-mono text-xs"
                  />
                  <p className="text-[11px] text-muted-foreground mt-1">Default 2e-5 prevents catastrophic forgetting.</p>
                </div>

                <div>
                  <label className="text-xs font-semibold mb-2 block">Entities to Include</label>
                  <div className="space-y-2 text-xs">
                    {availableEntityNames.map((ent) => (
                      <label key={ent} className="flex items-center gap-2 cursor-pointer capitalize">
                        <input
                          type="checkbox"
                          checked={selectedEntities.includes(ent)}
                          disabled={isTraining}
                          onChange={(e) => {
                            if (ent === "all") {
                              setSelectedEntities(e.target.checked ? ["all"] : []);
                            } else {
                              const withoutAll = selectedEntities.filter((x) => x !== "all");
                              if (e.target.checked) {
                                setSelectedEntities([...withoutAll, ent]);
                              } else {
                                setSelectedEntities(withoutAll.filter((x) => x !== ent));
                              }
                            }
                          }}
                        />
                        <span>{ent === "all" ? "All Entity Domains" : ent}</span>
                      </label>
                    ))}
                  </div>
                </div>

                <Button
                  className="w-full gap-2 mt-4"
                  onClick={() => startTrainMutation.mutate()}
                  disabled={isTraining || startTrainMutation.isPending}
                >
                  {isTraining ? (
                    <>
                      <Activity className="h-4 w-4 animate-spin" /> Training in Progress...
                    </>
                  ) : (
                    <>
                      <Play className="h-4 w-4 fill-current" /> Start Model Retraining
                    </>
                  )}
                </Button>
              </CardContent>
            </Card>

            {/* Live Progress & Terminal Logs */}
            <Card className="lg:col-span-2 flex flex-col">
              <CardHeader className="pb-3 border-b">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Terminal className="h-4 w-4 text-emerald-500" />
                    <CardTitle className="text-base font-mono">Live Training Execution Stream</CardTitle>
                  </div>
                  <Badge
                    variant={
                      trainStatus?.status === "completed"
                        ? "default"
                        : trainStatus?.status === "failed"
                        ? "destructive"
                        : trainStatus?.status === "running"
                        ? "secondary"
                        : "outline"
                    }
                    className="capitalize font-mono text-[11px]"
                  >
                    {trainStatus?.status || "Idle"}
                  </Badge>
                </div>
              </CardHeader>

              <CardContent className="p-4 space-y-4 flex-1 flex flex-col">
                <div className="space-y-2 bg-muted/30 p-3 rounded-lg border">
                  <div className="flex justify-between text-xs font-mono">
                    <span>
                      Epoch: {trainStatus?.current_epoch || 0} / {trainStatus?.total_epochs || 3}
                    </span>
                    <span>Progress: {trainStatus?.progress || 0}%</span>
                    <span>Loss: {trainStatus?.current_loss || "0.0000"}</span>
                    {isTraining && trainStatus?.phase && (
                      <span className="capitalize">Phase: {trainStatus.phase.replace(/_/g, " ")}</span>
                    )}
                  </div>
                  <Progress value={trainStatus?.progress || 0} className="h-2.5" />
                </div>

                {trainStatus?.evaluation && (
                  <div className="p-3 rounded-lg border bg-card text-xs space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold">Evaluation: starting model → fine-tuned</span>
                      <Badge variant="outline" className="text-[10px]">
                        {trainStatus.evaluation.split === "holdout"
                          ? `held-out · ${trainStatus.evaluation.finetuned?.num_queries ?? 0} queries`
                          : "training queries (optimistic)"}
                      </Badge>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      {(
                        [
                          ["Recall@1", "recall_at_1"],
                          ["Recall@5", "recall_at_5"],
                          ["MRR@10", "mrr_at_10"],
                        ] as const
                      ).map(([label, key]) => (
                        <div key={key}>
                          <span className="text-muted-foreground block">{label}</span>
                          <MetricDelta
                            base={trainStatus.evaluation?.base?.[key]}
                            tuned={trainStatus.evaluation?.finetuned?.[key]}
                          />
                        </div>
                      ))}
                    </div>
                    {trainStatus.evaluation.split === "train" && (
                      <p className="text-[11px] text-amber-600">
                        Fewer than 10 distinct queries, so these scores were measured on the training data. Add more
                        pairs to get a held-out score.
                      </p>
                    )}
                  </div>
                )}

                <div
                  ref={logTerminalRef}
                  className="flex-1 min-h-[300px] max-h-[420px] overflow-y-auto bg-black text-emerald-400 p-4 rounded-lg font-mono text-xs space-y-1 shadow-inner border border-emerald-950/60"
                >
                  {trainStatus?.logs?.length ? (
                    trainStatus.logs.map((l, i) => (
                      <div key={i} className="flex gap-2">
                        <span className="text-zinc-600 select-none">[{l.timestamp}]</span>
                        <span
                          className={
                            l.level === "error" ? "text-red-400 font-bold" : l.level === "warning" ? "text-amber-400" : ""
                          }
                        >
                          {l.message}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="text-zinc-600 italic">Awaiting training launch... Click 'Start Model Retraining' to begin.</div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* ═════════════════════════════════════════════════════════════════════
            TAB 4: MODEL REGISTRY & SIDE-BY-SIDE SANDBOX
        ═════════════════════════════════════════════════════════════════════ */}
        <TabsContent value="models" className="space-y-6">
          <Card>
            <CardHeader className="pb-3 border-b flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">Model Checkpoint Registry</CardTitle>
                <CardDescription className="text-xs">
                  All trained model versions saved on disk. Deploy any checkpoint with zero downtime hot-swapping.
                </CardDescription>
              </div>
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 text-xs"
                onClick={() => activateModelMutation.mutate("base-minilm")}
              >
                <RotateCcw className="h-3.5 w-3.5" /> Revert to Base Model
              </Button>
            </CardHeader>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b bg-muted/40 text-muted-foreground text-left">
                    <th className="py-2.5 px-4 font-semibold">Checkpoint ID</th>
                    <th className="py-2.5 px-4 font-semibold">Trained Entities</th>
                    <th className="py-2.5 px-4 font-semibold">Pairs</th>
                    <th className="py-2.5 px-4 font-semibold">Final Loss</th>
                    <th className="py-2.5 px-4 font-semibold">Recall@1 (before → after)</th>
                    <th className="py-2.5 px-4 font-semibold">Status</th>
                    <th className="py-2.5 px-4 font-semibold text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {checkpoints.map((ckpt) => (
                    <tr key={ckpt.id} className="hover:bg-muted/20">
                      <td className="py-3 px-4 font-mono font-medium">
                        {ckpt.name}
                        {ckpt.is_base && <span className="ml-2 text-zinc-500 font-normal">(Default)</span>}
                      </td>
                      <td className="py-3 px-4">
                        <div className="flex gap-1 flex-wrap">
                          {ckpt.entities_trained?.map((e) => (
                            <Badge key={e} variant="outline" className="text-[10px] capitalize">
                              {e}
                            </Badge>
                          ))}
                        </div>
                      </td>
                      <td className="py-3 px-4 font-mono">{ckpt.pair_count}</td>
                      <td className="py-3 px-4 font-mono">
                        {ckpt.final_loss ? ckpt.final_loss.toFixed(4) : "—"}
                      </td>
                      <td className="py-3 px-4">
                        {ckpt.evaluation ? (
                          <span title={ckpt.evaluation.split === "train" ? "Measured on training queries" : "Held-out queries"}>
                            <MetricDelta
                              base={ckpt.evaluation.base?.recall_at_1}
                              tuned={ckpt.evaluation.finetuned?.recall_at_1}
                            />
                            {ckpt.evaluation.split === "train" && <span className="text-amber-600 ml-1">*</span>}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="py-3 px-4">
                        {ckpt.is_active ? (
                          <Badge variant="default" className="bg-emerald-600 hover:bg-emerald-700 text-[10px] gap-1">
                            <CheckCircle2 className="h-3 w-3" /> Live Serving
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-[11px]">Ready</span>
                        )}
                      </td>
                      <td className="py-3 px-4 text-right">
                        {!ckpt.is_active && (
                          <Button
                            size="sm"
                            variant="secondary"
                            className="h-7 text-xs gap-1"
                            disabled={activateModelMutation.isPending}
                            onClick={() => activateModelMutation.mutate(ckpt.id)}
                          >
                            <ArrowRight className="h-3 w-3" /> Deploy / Hot-Swap
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Side-by-Side Test Query Sandbox */}
          <Card>
            <CardHeader className="pb-3 border-b">
              <CardTitle className="text-base flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" /> Test Query Evaluation Sandbox
              </CardTitle>
              <CardDescription className="text-xs">
                Run a query against live search. Pick a candidate checkpoint to see how it re-ranks the same results
                compared with the active model before you deploy it.
              </CardDescription>
            </CardHeader>
            <CardContent className="p-6 space-y-4">
              <div className="flex flex-col md:flex-row gap-2">
                <Input
                  value={sandboxQuery}
                  onChange={(e) => setSandboxQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleTestQuery()}
                  placeholder="Enter test query (e.g. 'Steel Hinges' or 'industrial valve')..."
                  className="text-sm md:max-w-md"
                />
                <Select value={sandboxCandidate} onValueChange={setSandboxCandidate}>
                  <SelectTrigger className="md:w-72 text-xs">
                    <SelectValue placeholder="Compare with..." />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No comparison (live results only)</SelectItem>
                    {checkpoints
                      .filter((c) => !c.is_active)
                      .map((c) => (
                        <SelectItem key={c.id} value={c.id}>
                          Compare with {c.is_base ? "Base Model" : c.id}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
                <Button onClick={handleTestQuery} disabled={isTestingQuery} className="gap-2">
                  <Search className="h-4 w-4" /> {isTestingQuery ? "Testing..." : "Run Test"}
                </Button>
              </div>

              {sandboxComparison && (
                <div className="space-y-3 pt-2">
                  <p className="text-[11px] text-muted-foreground">
                    {sandboxComparison.note} Pool: {sandboxComparison.pool_size} documents.
                  </p>
                  {sandboxComparison.pool_size === 0 ? (
                    <p className="text-xs text-muted-foreground">Live search returned nothing for this query.</p>
                  ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {(
                        [
                          [
                            `Active: ${activeCheckpoint?.is_base ? "Base Model" : activeCheckpoint?.id ?? "current"}`,
                            sandboxComparison.active,
                          ],
                          [`Candidate: ${sandboxComparison.candidate_checkpoint_id}`, sandboxComparison.candidate],
                        ] as const
                      ).map(([heading, rows], col) => (
                        <div key={heading} className="space-y-2">
                          <span className="text-xs font-semibold block">{heading}</span>
                          {rows.map((r) => (
                            <div
                              key={`${r.type}-${r.rank}-${r.title}`}
                              className="p-2.5 border rounded-lg bg-card/60 flex items-center justify-between gap-2"
                            >
                              <div className="min-w-0">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono text-[11px] text-muted-foreground w-5">#{r.rank}</span>
                                  <Badge variant="outline" className="text-[10px] uppercase font-mono">
                                    {r.type}
                                  </Badge>
                                  <span className="font-semibold text-sm truncate">{r.title}</span>
                                </div>
                                {r.subtitle && (
                                  <p className="text-xs text-muted-foreground mt-0.5 ml-7 truncate">{r.subtitle}</p>
                                )}
                              </div>
                              <div className="flex items-center gap-1.5 shrink-0">
                                {col === 1 && r.rank_change !== undefined && r.rank_change !== 0 && (
                                  <span
                                    className={`font-mono text-[11px] ${
                                      r.rank_change > 0 ? "text-emerald-600" : "text-destructive"
                                    }`}
                                    title={`Was #${r.active_rank} with the active model`}
                                  >
                                    {r.rank_change > 0 ? "▲" : "▼"}
                                    {Math.abs(r.rank_change)}
                                  </span>
                                )}
                                <Badge variant="secondary" className="font-mono text-xs">
                                  {r.score.toFixed(0)}%
                                </Badge>
                              </div>
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {!sandboxComparison && sandboxResults.length > 0 && (
                <div className="space-y-3 pt-2">
                  <span className="text-xs font-semibold text-muted-foreground block">
                    Live Search Matches ({sandboxResults.length} items):
                  </span>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {sandboxResults.map((r, i) => (
                      <div key={i} className="p-3 border rounded-lg bg-card/60 flex items-center justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <Badge variant="outline" className="text-[10px] uppercase font-mono">
                              {r.type}
                            </Badge>
                            <h4 className="font-bold text-sm">{r.title}</h4>
                          </div>
                          {r.subtitle && <p className="text-xs text-muted-foreground mt-0.5">{r.subtitle}</p>}
                        </div>
                        <Badge variant="secondary" className="font-mono text-xs">
                          {r.matchType} · {r.matchPercentage?.toFixed(0)}%
                        </Badge>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

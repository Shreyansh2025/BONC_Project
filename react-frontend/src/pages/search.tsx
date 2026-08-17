import { useState, useEffect } from "react";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { Search as SearchIcon, MapPin, Building2, Package, ImageOff } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Pagination,
  PaginationContent,
  PaginationItem,
  PaginationLink,
  PaginationNext,
  PaginationPrevious,
} from "@/components/ui/pagination";

// ─── Types ──────────────────────────────────────────────────────────────
interface SearchResult {
  _id: string | number;
  id?: string | number;
  type: "business" | "product";
  image?: string | null;

  // business fields
  businessName?: string;
  name?: string;
  tagline?: string;
  address1?: string;
  city?: string;
  state?: string;
  country?: string;
  pincode?: string;

  // product fields
  productName?: string;
  slug?: string;
  categoryName?: string;
  brandName?: string;
  businessName_product?: string;
  minPrice?: string;
  maxPrice?: string;

  // shared
  description?: string;
  matchType?: string;
  matchPercentage?: number;
  matchedKeyword?: string;
}

// Exact 5-field response contract
interface SearchResponse {
  Success: boolean;
  Message: string;
  Type: string;
  Data: SearchResult[];
  TotalCount: number;
}

const PAGE_SIZE = 10;

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function stripHtml(raw?: string): string {
  if (!raw) return "";
  return raw
    .replace(/<[^>]*>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
}

async function fetchSearch(query: string, page: number, type: string): Promise<SearchResponse> {
  const res = await fetch(`/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      SearchText: query,
      PageNo: page,
      PageSize: PAGE_SIZE,
      Type: type,
    }),
  });
  if (!res.ok) throw new Error("Search request failed");
  return res.json();
}

function matchBadgeVariant(matchType?: string) {
  if (matchType === "Exact Match") return "default" as const;
  if (matchType === "Partial Word Match") return "secondary" as const;
  return "outline" as const;
}

function EmptyState({ icon: Icon, title, subtitle }: { icon: typeof SearchIcon; title: string; subtitle: string }) {
  return (
    <div className="text-center py-24 border-2 border-dashed rounded-lg bg-muted/10">
      <Icon className="mx-auto h-12 w-12 text-muted-foreground mb-4 opacity-50" />
      <h3 className="text-lg font-bold mb-1">{title}</h3>
      <p className="text-sm text-muted-foreground">{subtitle}</p>
    </div>
  );
}

function ResultSkeletons() {
  return (
    <div className="space-y-4">
      {[1, 2, 3].map((i) => (
        <Card key={i}>
          <CardContent className="p-5 flex gap-4">
            <Skeleton className="h-20 w-20 rounded-md shrink-0" />
            <div className="flex-1">
              <Skeleton className="h-5 w-1/3 mb-3" />
              <Skeleton className="h-4 w-2/3 mb-2" />
              <Skeleton className="h-4 w-full" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function ResultThumb({ src, alt }: { src?: string | null; alt: string }) {
  const [failed, setFailed] = useState(false);

  let finalSrc = src;
  if (finalSrc && !finalSrc.startsWith("http") && !finalSrc.startsWith("/api/uploads")) {
    const path = finalSrc.startsWith("/") ? finalSrc : `/${finalSrc}`;
    finalSrc = `https://api.boncnetwork.com${path}`;
  }

  if (!finalSrc || failed) {
    return (
      <div className="h-20 w-20 rounded-md bg-muted flex items-center justify-center shrink-0">
        <ImageOff className="h-6 w-6 text-muted-foreground/50" />
      </div>
    );
  }
  return (
    <img
      src={finalSrc}
      alt={alt}
      onError={() => setFailed(true)}
      className="h-20 w-20 rounded-md object-cover shrink-0 border"
    />
  );
}

function BusinessCard({ item }: { item: SearchResult }) {
  const name =
    item.name ||
    (item.businessName && item.businessName.trim() && item.businessName !== "1"
      ? item.businessName
      : "Unknown Business");
  const location = [item.address1, item.city, item.state, item.country, item.pincode].filter(Boolean).join(", ");
  const description = stripHtml(item.description);

  return (
    <Card className="hover-elevate transition-all">
      <CardContent className="p-5 flex gap-4">
        <ResultThumb src={item.image} alt={name} />
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-4 mb-2">
            <div className="flex items-center gap-2 min-w-0">
              <Badge variant="outline" className="shrink-0 text-[10px] gap-1">
                <Building2 className="h-3 w-3" /> Business
              </Badge>
              <h3 className="font-bold text-lg leading-tight truncate">{name}</h3>
            </div>
            {item.matchPercentage !== undefined && (
              <Badge variant={matchBadgeVariant(item.matchType)} className="shrink-0 font-mono text-[10px]">
                {item.matchType} · {item.matchPercentage.toFixed(0)}%
              </Badge>
            )}
          </div>

          {item.tagline && <p className="text-sm italic text-muted-foreground mb-2">{stripHtml(item.tagline)}</p>}

          {location && (
            <div className="flex items-center gap-1.5 text-sm text-muted-foreground mb-2">
              <MapPin className="h-3.5 w-3.5 shrink-0" />
              <span>{location}</span>
            </div>
          )}

          {description && <p className="text-sm text-foreground/80 line-clamp-3">{description}</p>}

          {item.matchedKeyword && (
            <p className="text-xs text-muted-foreground mt-3 font-mono">Matched: {item.matchedKeyword}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ProductCard({ item }: { item: SearchResult }) {
  const hasPriceRange =
    item.minPrice && item.maxPrice && !(item.minPrice === "0.00" && item.maxPrice === "0.00");
  const description = stripHtml(item.description);
  const href = item.slug ? `/products/${item.slug}` : undefined;
  const name = item.name || item.productName || "Unknown Product";

  return (
    <Card className="hover-elevate transition-all">
      <CardContent className="p-5 flex gap-4">
        <ResultThumb src={item.image} alt={name} />
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-4 mb-2">
            <div className="flex items-center gap-2 min-w-0">
              <Badge variant="outline" className="shrink-0 text-[10px] gap-1">
                <Package className="h-3 w-3" /> Product
              </Badge>
              {href ? (
                <a href={href} className="font-bold text-lg leading-tight truncate hover:underline">
                  {name}
                </a>
              ) : (
                <h3 className="font-bold text-lg leading-tight truncate">{name}</h3>
              )}
            </div>
            {item.matchPercentage !== undefined && (
              <Badge variant={matchBadgeVariant(item.matchType)} className="shrink-0 font-mono text-[10px]">
                {item.matchType} · {item.matchPercentage.toFixed(0)}%
              </Badge>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground mb-2">
            {item.categoryName && (
              <Badge variant="outline" className="font-mono text-[10px]">
                {item.categoryName.split(",")[0]}
              </Badge>
            )}
            {item.brandName && <span>{item.brandName}</span>}
            {hasPriceRange && (
              <span className="font-mono">
                ₹{item.minPrice} – ₹{item.maxPrice}
              </span>
            )}
          </div>

          {description && <p className="text-sm text-foreground/80 line-clamp-3 mb-2">{description}</p>}

          {item.businessName && <p className="text-xs text-muted-foreground">By: {item.businessName}</p>}

          {item.matchedKeyword && (
            <p className="text-xs text-muted-foreground mt-3 font-mono">Matched: {item.matchedKeyword}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// Client computes pagination state from TotalCount and active page
function ResultsPagination({
  totalCount,
  currentPage,
  pageSize,
  onPageChange,
}: {
  totalCount: number;
  currentPage: number;
  pageSize: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  if (totalPages <= 1) return null;

  const has_previous = currentPage > 1;
  const has_next = currentPage < totalPages;

  const pages: number[] = [];
  const start = Math.max(1, currentPage - 2);
  const end = Math.min(totalPages, currentPage + 2);
  for (let p = start; p <= end; p++) pages.push(p);

  return (
    <Pagination className="pt-2">
      <PaginationContent>
        <PaginationItem>
          <PaginationPrevious
            href="#"
            aria-disabled={!has_previous}
            className={!has_previous ? "pointer-events-none opacity-50" : undefined}
            onClick={(e) => {
              e.preventDefault();
              if (has_previous) onPageChange(currentPage - 1);
            }}
          />
        </PaginationItem>

        {pages.map((p) => (
          <PaginationItem key={p}>
            <PaginationLink
              href="#"
              isActive={p === currentPage}
              onClick={(e) => {
                e.preventDefault();
                onPageChange(p);
              }}
            >
              {p}
            </PaginationLink>
          </PaginationItem>
        ))}

        <PaginationItem>
          <PaginationNext
            href="#"
            aria-disabled={!has_next}
            className={!has_next ? "pointer-events-none opacity-50" : undefined}
            onClick={(e) => {
              e.preventDefault();
              if (has_next) onPageChange(currentPage + 1);
            }}
          />
        </PaginationItem>
      </PaginationContent>
    </Pagination>
  );
}

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [filter, setFilter] = useState<"all" | "business" | "product">("all");
  const debouncedQuery = useDebouncedValue(query.trim(), 350);

  useEffect(() => {
    setPage(1);
  }, [debouncedQuery, filter]);

  const { data, isFetching } = useQuery({
    queryKey: ["search", debouncedQuery, page, filter],
    queryFn: () => fetchSearch(debouncedQuery, page, filter),
    enabled: debouncedQuery.length > 0,
    placeholderData: keepPreviousData,
  });

  const results = data?.Data ?? [];
  const totalCount = data?.TotalCount ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  return (
    <div className="container py-8 max-w-5xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Search</h1>
        <p className="text-muted-foreground">Search companies and products together — results are ranked and combined.</p>
      </div>

      <div className="relative max-w-xl">
        <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="e.g. 'Steel Hinges' or 'Electronics in Mumbai'"
          className="pl-10"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
      </div>

      {/* FILTER BUTTONS */}
      <div className="flex gap-2">
        <Badge
          variant={filter === "all" ? "default" : "outline"}
          className="cursor-pointer text-sm py-1"
          onClick={() => setFilter("all")}
        >
          All Results
        </Badge>
        <Badge
          variant={filter === "business" ? "default" : "outline"}
          className="cursor-pointer text-sm py-1"
          onClick={() => setFilter("business")}
        >
          <Building2 className="h-3 w-3 mr-1" /> Companies
        </Badge>
        <Badge
          variant={filter === "product" ? "default" : "outline"}
          className="cursor-pointer text-sm py-1"
          onClick={() => setFilter("product")}
        >
          <Package className="h-3 w-3 mr-1" /> Products
        </Badge>
      </div>

      {debouncedQuery.length === 0 ? (
        <EmptyState
          icon={SearchIcon}
          title="Search companies & products"
          subtitle="Start typing a name, category, or city."
        />
      ) : isFetching && !data ? (
        <ResultSkeletons />
      ) : results.length === 0 ? (
        <EmptyState icon={SearchIcon} title="No results found" subtitle="Try a different search term." />
      ) : (
        <>
          {data && (
            <p className="text-sm text-muted-foreground">
              {totalCount} result{totalCount === 1 ? "" : "s"} · page {page} of {totalPages}
            </p>
          )}

          <div className={`space-y-4 ${isFetching ? "opacity-60" : ""}`}>
            {results.map((item) =>
              item.type === "business" ? (
                <BusinessCard key={`business-${item.id || item._id}`} item={item} />
              ) : (
                <ProductCard key={`product-${item.id || item._id}`} item={item} />
              ),
            )}
          </div>

          {data && (
            <ResultsPagination
              totalCount={totalCount}
              currentPage={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
            />
          )}
        </>
      )}
    </div>
  );
}
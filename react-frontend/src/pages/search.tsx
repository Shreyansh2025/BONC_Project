import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search as SearchIcon, MapPin, Building2, Package } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";

interface CompanyResult {
  _id: string | number;
  businessName?: string;
  tagline?: string;
  description?: string;
  address1?: string;
  city?: string;
  state?: string;
  country?: string;
  pincode?: string;
  matchType?: string;
  matchPercentage?: number;
  matchedKeyword?: string;
}

interface ProductResult {
  _id: string | number;
  productName?: string;
  categoryName?: string;
  brandName?: string;
  businessName?: string;
  description?: string;
  minPrice?: string;
  maxPrice?: string;
  matchType?: string;
  matchPercentage?: number;
  matchedKeyword?: string;
}

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
  return raw.replace(/<[^>]*>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});/gi, " ").replace(/\s+/g, " ").trim();
}

async function fetchCompanies(query: string): Promise<CompanyResult[]> {
  const res = await fetch(`/api/search/companies?q=${encodeURIComponent(query)}`);
  if (!res.ok) throw new Error("Search request failed");
  return res.json();
}

async function fetchProducts(query: string): Promise<ProductResult[]> {
  const res = await fetch(`/api/search/products?q=${encodeURIComponent(query)}`);
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
          <CardContent className="p-5">
            <Skeleton className="h-5 w-1/3 mb-3" />
            <Skeleton className="h-4 w-2/3 mb-2" />
            <Skeleton className="h-4 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function CompanySearchTab() {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query.trim(), 350);

  const { data: results, isFetching } = useQuery({
    queryKey: ["search-companies", debouncedQuery],
    queryFn: () => fetchCompanies(debouncedQuery),
    enabled: debouncedQuery.length > 0,
  });

  return (
    <div className="space-y-6">
      <div className="relative max-w-xl">
        <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="e.g. 'Electronics in Mumbai'"
          className="pl-10"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
      </div>

      {debouncedQuery.length === 0 ? (
        <EmptyState icon={Building2} title="Search companies" subtitle="Start typing a company name, category, or city." />
      ) : isFetching ? (
        <ResultSkeletons />
      ) : !results || results.length === 0 ? (
        <EmptyState icon={Building2} title="No companies found" subtitle="Try a different search term." />
      ) : (
        <div className="space-y-4">
          {results.map((company) => {
            const name =
              company.businessName && company.businessName.trim() && company.businessName !== "1"
                ? company.businessName
                : "Unknown Business";
            const location = [company.address1, company.city, company.state, company.country, company.pincode]
              .filter(Boolean)
              .join(", ");
            const description = stripHtml(company.description);

            return (
              <Card key={company._id} className="hover-elevate transition-all">
                <CardContent className="p-5">
                  <div className="flex items-start justify-between gap-4 mb-2">
                    <h3 className="font-bold text-lg leading-tight">{name}</h3>
                    {company.matchPercentage !== undefined && (
                      <Badge variant={matchBadgeVariant(company.matchType)} className="shrink-0 font-mono text-[10px]">
                        {company.matchType} · {company.matchPercentage.toFixed(0)}%
                      </Badge>
                    )}
                  </div>

                  {company.tagline && (
                    <p className="text-sm italic text-muted-foreground mb-2">{stripHtml(company.tagline)}</p>
                  )}

                  {location && (
                    <div className="flex items-center gap-1.5 text-sm text-muted-foreground mb-2">
                      <MapPin className="h-3.5 w-3.5 shrink-0" />
                      <span>{location}</span>
                    </div>
                  )}

                  {description && <p className="text-sm text-foreground/80 line-clamp-3">{description}</p>}

                  {company.matchedKeyword && (
                    <p className="text-xs text-muted-foreground mt-3 font-mono">Matched: {company.matchedKeyword}</p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ProductSearchTab() {
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query.trim(), 350);

  const { data: results, isFetching } = useQuery({
    queryKey: ["search-products", debouncedQuery],
    queryFn: () => fetchProducts(debouncedQuery),
    enabled: debouncedQuery.length > 0,
  });

  return (
    <div className="space-y-6">
      <div className="relative max-w-xl">
        <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="e.g. 'Steel Hinges'"
          className="pl-10"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {debouncedQuery.length === 0 ? (
        <EmptyState icon={Package} title="Search products" subtitle="Start typing a product name or category." />
      ) : isFetching ? (
        <ResultSkeletons />
      ) : !results || results.length === 0 ? (
        <EmptyState icon={Package} title="No products found" subtitle="Try a different search term." />
      ) : (
        <div className="space-y-4">
          {results.map((product) => {
            const hasPriceRange =
              product.minPrice && product.maxPrice && !(product.minPrice === "0.00" && product.maxPrice === "0.00");
            const description = stripHtml(product.description);

            return (
              <Card key={product._id} className="hover-elevate transition-all">
                <CardContent className="p-5">
                  <div className="flex items-start justify-between gap-4 mb-2">
                    <h3 className="font-bold text-lg leading-tight">{product.productName || "Unknown Product"}</h3>
                    {product.matchPercentage !== undefined && (
                      <Badge variant={matchBadgeVariant(product.matchType)} className="shrink-0 font-mono text-[10px]">
                        {product.matchType} · {product.matchPercentage.toFixed(0)}%
                      </Badge>
                    )}
                  </div>

                  <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground mb-2">
                    {product.categoryName && (
                      <Badge variant="outline" className="font-mono text-[10px]">
                        {product.categoryName.split(",")[0]}
                      </Badge>
                    )}
                    {product.brandName && <span>{product.brandName}</span>}
                    {hasPriceRange && (
                      <span className="font-mono">
                        ₹{product.minPrice} – ₹{product.maxPrice}
                      </span>
                    )}
                  </div>

                  {description && <p className="text-sm text-foreground/80 line-clamp-3 mb-2">{description}</p>}

                  {product.businessName && (
                    <p className="text-xs text-muted-foreground">By: {product.businessName}</p>
                  )}

                  {product.matchedKeyword && (
                    <p className="text-xs text-muted-foreground mt-3 font-mono">Matched: {product.matchedKeyword}</p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default function SearchPage() {
  return (
    <div className="container py-8 max-w-5xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Search</h1>
        <p className="text-muted-foreground">Search the B2B company directory or your extracted products.</p>
      </div>

      <Tabs defaultValue="companies">
        <TabsList>
          <TabsTrigger value="companies">Companies</TabsTrigger>
          <TabsTrigger value="products">Products</TabsTrigger>
        </TabsList>
        <TabsContent value="companies" className="mt-6">
          <CompanySearchTab />
        </TabsContent>
        <TabsContent value="products" className="mt-6">
          <ProductSearchTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
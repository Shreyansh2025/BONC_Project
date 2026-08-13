import { useState } from "react";
import { useRoute, Link } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, Database, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ProductGallery } from "@/components/ProductGallery";

// ─── Types ──────────────────────────────────────────────────────────────
// Matches serialize_row("Products", ...) shape from GET /api/products/{id_or_slug}.
// Kept local rather than pulling from the generated client since that
// client hasn't been regenerated from the OpenAPI spec for this new route yet.
interface ProductDetail {
  _id: string | number;
  productName: string;
  category?: string;
  model?: string | null;
  description?: string;
  price?: string | null;
  features?: string[];
  specifications?: Record<string, unknown>;
  images?: string[];
  imagePath?: string | null;
  slug?: string;
  sourceFileName?: string;
  createdDate?: string;
}

async function fetchProduct(idOrSlug: string): Promise<ProductDetail> {
  const res = await fetch(`/api/products/${encodeURIComponent(idOrSlug)}`);
  if (res.status === 404) {
    throw new Error("NOT_FOUND");
  }
  if (!res.ok) {
    throw new Error("Failed to load product");
  }
  return res.json();
}

function DetailSkeleton() {
  return (
    <div className="container py-8 max-w-5xl mx-auto space-y-6">
      <Skeleton className="h-8 w-40" />
      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <Skeleton className="h-80 w-full rounded-lg" />
        <div className="space-y-4">
          <Skeleton className="h-8 w-3/4" />
          <Skeleton className="h-4 w-1/3" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    </div>
  );
}

export default function ProductDetailPage() {
  // Powers both /products/:slug (Search links, which only have a slug)
  // and Library links (which pass the numeric _id) — the backend route
  // tries numeric id first, then slug, so either works here unchanged.
  const [, params] = useRoute("/products/:idOrSlug");
  const idOrSlug = params?.idOrSlug ?? "";
  const [expandedImg, setExpandedImg] = useState<string | null>(null);

  const { data: product, isLoading, isError, error } = useQuery({
    queryKey: ["product", idOrSlug],
    queryFn: () => fetchProduct(idOrSlug),
    enabled: !!idOrSlug,
    retry: false,
  });

  if (isLoading) return <DetailSkeleton />;

  if (isError || !product) {
    const notFound = error instanceof Error && error.message === "NOT_FOUND";
    return (
      <div className="container py-24 max-w-2xl mx-auto text-center space-y-4">
        <AlertCircle className="mx-auto h-12 w-12 text-muted-foreground opacity-50" />
        <h1 className="text-xl font-bold">
          {notFound ? "Product not found" : "Couldn't load this product"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {notFound
            ? "This product isn't in your local library — it may only exist in the imported B2B catalog, which doesn't have a detail page yet."
            : "Something went wrong fetching this product."}
        </p>
        <Link href="/search">
          <Button variant="outline" className="gap-2">
            <ArrowLeft className="h-4 w-4" /> Back to Search
          </Button>
        </Link>
      </div>
    );
  }

  const images = product.images && product.images.length > 0
    ? product.images
    : product.imagePath
      ? [product.imagePath]
      : [];

  const specs = product.specifications && Object.keys(product.specifications).length > 0
    ? Object.entries(product.specifications)
    : [];

  return (
    <div className="container py-8 max-w-5xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <Link href="/search">
        <Button variant="ghost" size="sm" className="gap-2 -ml-2">
          <ArrowLeft className="h-4 w-4" /> Back
        </Button>
      </Link>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
        <Card className="overflow-hidden">
          <div className="bg-muted/30 p-3">
            {images.length > 0 ? (
              <ProductGallery images={images} onExpand={(url) => setExpandedImg(url)} />
            ) : (
              <div className="aspect-[4/3] flex items-center justify-center">
                <Database className="w-16 h-16 text-muted-foreground/30" />
              </div>
            )}
          </div>
        </Card>

        <div className="space-y-4">
          <div>
            <div className="flex items-start justify-between gap-3 mb-2">
              <h1 className="text-2xl font-bold tracking-tight">{product.productName}</h1>
              {product.price && (
                <span className="shrink-0 text-lg font-mono font-semibold text-primary">
                  {product.price}
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {product.category && <Badge variant="outline">{product.category}</Badge>}
              {product.model && (
                <Badge variant="outline" className="font-mono text-[10px]">
                  {product.model}
                </Badge>
              )}
            </div>
          </div>

          {product.description && (
            <p className="text-sm text-foreground/80 leading-relaxed">{product.description}</p>
          )}

          {product.features && product.features.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold mb-2">Features</h3>
              <ul className="text-sm text-muted-foreground list-disc list-inside space-y-1">
                {product.features.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            </div>
          )}

          {specs.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold mb-2">Specifications</h3>
              <Card>
                <CardContent className="p-4 text-sm space-y-1.5 font-mono">
                  {specs.map(([key, val]) => (
                    <div key={key} className="flex justify-between gap-4">
                      <span className="text-muted-foreground">{key}</span>
                      <span className="font-medium text-right">{String(val)}</span>
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
          )}

          {product.sourceFileName && (
            <p className="text-xs text-muted-foreground pt-2 border-t">
              Extracted from: {product.sourceFileName}
            </p>
          )}
        </div>
      </div>

      {expandedImg && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex items-center justify-center p-4"
          onClick={() => setExpandedImg(null)}
        >
          <button
            className="absolute top-4 right-4 text-white/70 hover:text-white"
            onClick={() => setExpandedImg(null)}
          >
            <X className="w-7 h-7" />
          </button>
          <img
            src={expandedImg}
            alt=""
            className="max-w-full max-h-[90vh] object-contain rounded shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}
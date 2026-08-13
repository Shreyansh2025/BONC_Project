import { useState } from "react";
import { Link } from "wouter";
import {
  useListProducts, useDeleteProduct, useUpdateProductImages,
  getListProductsQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Search, Filter, Trash2, Database, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/hooks/use-toast";
import { ProductGallery } from "@/components/ProductGallery";

export default function LibraryPage() {
  const { data: products, isLoading } = useListProducts();
  const deleteMutation = useDeleteProduct();
  const updateImagesMutation = useUpdateProductImages();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const [search, setSearch] = useState("");
  const [expandedImg, setExpandedImg] = useState<string | null>(null);
  
  // Track expanded cards for specs
  const [expandedSpecs, setExpandedSpecs] = useState<Record<string, boolean>>({});

  const toggleSpecs = (id: string) => {
    setExpandedSpecs(prev => ({ ...prev, [id]: !prev[id] }));
  };

  // ... (keep your existing handlers like persistImages and handleDelete)
  const persistImages = (id: string, images: string[]) => {
    updateImagesMutation.mutate({ id, data: { images } }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
      },
      onError: (err) => {
        toast({ title: "Failed to update images", description: err.message, variant: "destructive" });
      },
    });
  };

  const handleDelete = (id: string) => {
    if (!confirm("Delete this product?")) return;
    
    deleteMutation.mutate({ id }, {
      onSuccess: () => {
        toast({ title: "Product deleted" });
        queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
      },
      onError: (err) => {
        toast({ title: "Failed to delete", description: err.message, variant: "destructive" });
      }
    });
  };

  const filteredProducts = products?.filter(p => 
    (p.productName ?? "").toLowerCase().includes(search.toLowerCase()) || 
    (p.category ?? "").toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="container py-8 max-w-7xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Product Library</h1>
          <p className="text-muted-foreground">Browse structured data extracted from your brochures.</p>
        </div>
        <div className="relative w-full sm:w-72">
          <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input 
            placeholder="Search products..." 
            className="pl-9"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {[1, 2, 3, 4, 5, 6].map(i => (
            <Card key={i} className="overflow-hidden">
              <Skeleton className="h-48 w-full rounded-none" />
              <CardContent className="p-6">
                <Skeleton className="h-6 w-3/4 mb-4" />
                <Skeleton className="h-4 w-1/2 mb-2" />
                <Skeleton className="h-4 w-full" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : !products || products.length === 0 ? (
        <div className="text-center py-24 border-2 border-dashed rounded-lg bg-muted/10">
          <Database className="mx-auto h-12 w-12 text-muted-foreground mb-4 opacity-50" />
          <h3 className="text-lg font-bold mb-1">No products found</h3>
          <p className="text-sm text-muted-foreground">
            Extract some products from a brochure first.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {filteredProducts?.map((product) => (
            <Card key={product._id} className="flex flex-col overflow-hidden hover-elevate transition-all border-border hover:border-primary/50">
              <div className="relative bg-muted/30 p-3 border-b">
                {product.images && product.images.length > 0 ? (
                  <ProductGallery
                    images={product.images}
                    onReorder={(next) => persistImages(product._id, next)}
                    onRemove={(idx) =>
                      persistImages(product._id, (product.images ?? []).filter((_, i) => i !== idx))
                    }
                    onExpand={(url) => setExpandedImg(url)}
                  />
                ) : (
                  <div className="aspect-[4/3] flex items-center justify-center">
                    <Database className="w-12 h-12 text-muted-foreground/30" />
                  </div>
                )}
                <div className="absolute top-2 right-2">
                  <Badge className="font-mono text-[10px] shadow-sm">{product.category}</Badge>
                </div>
              </div>
              <CardContent className="flex-1 p-5">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <h3 className="font-bold text-lg leading-tight line-clamp-2" title={product.productName}>
                    <Link href={`/products/${product._id}`} className="hover:underline hover:text-primary">
                      {product.productName}
                    </Link>
                  </h3>
                  {product.price && (
                    <span className="shrink-0 text-sm font-mono font-semibold text-primary">{product.price}</span>
                  )}
                </div>

                {product.model && (
                  <Badge variant="outline" className="font-mono text-[10px] mb-2">{product.model}</Badge>
                )}

                {product.description && (
                  <p className="text-sm text-muted-foreground line-clamp-2 mb-2">{product.description}</p>
                )}

                {product.specifications && Object.keys(product.specifications).length > 0 && (
                  <div className="mt-4 text-xs space-y-1.5 font-mono">
                    {Object.entries(product.specifications)
                      .slice(0, expandedSpecs[product._id] ? undefined : 3)
                      .map(([key, val]) => (
                        <div key={key} className="flex justify-between gap-2 overflow-hidden">
                          <span className="text-muted-foreground truncate">{key}</span>
                          <span className="font-medium text-right truncate">{val}</span>
                        </div>
                      ))}
                    {Object.keys(product.specifications).length > 3 && (
                      <button 
                        type="button"
                        onClick={() => toggleSpecs(product._id)}
                        className="text-primary hover:underline italic text-[10px] mt-1 block focus:outline-none"
                      >
                        {expandedSpecs[product._id] ? "Show less" : `+${Object.keys(product.specifications).length - 3} more`}
                      </button>
                    )}
                  </div>
                )}
              </CardContent>
              <CardFooter className="p-4 border-t bg-muted/20 flex justify-between items-center text-xs text-muted-foreground">
                <span className="truncate pr-2" title={product.sourceFileName}>
                  {product.sourceFileName || "Unknown source"}
                </span>
                <Button 
                  variant="ghost" 
                  size="icon" 
                  className="h-7 w-7 text-muted-foreground hover:text-destructive shrink-0"
                  onClick={() => handleDelete(product._id)}
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}

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
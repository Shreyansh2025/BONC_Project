import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { useDropzone } from "react-dropzone";
import {
  UploadCloud, CheckCircle2, ChevronRight, FileText,
  Loader2, Save, Maximize2, X, ChevronLeft, ChevronRight as ChevronRightIcon,
  Plus, Edit2, Crop, Trash2
} from "lucide-react";
import {
  usePreviewFile, useProcessFile, useSaveProducts,
  getListProductsQueryKey, getListBrochuresQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/hooks/use-toast";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription, CardFooter } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import { ProductGallery, MAX_PRODUCT_IMAGES } from "@/components/ProductGallery";
import { ImagePool } from "@/components/ImagePool";

const CATEGORIES = [
  "Medical", "Electronics", "Tractor", "Shoes", "Solar", "AI", "MRI",
  "Rice", "Agriculture", "Plant Equipment", "Automobile", "Machinery",
];

function detectCategory(filename: string): string {
  const name = filename.toLowerCase();
  const rules: [string[], string][] = [
    [["medical", "pharma", "hospital", "clinic", "health", "surgical"], "Medical"],
    [["mri", "magnetic", "resonance", "scanner", "imaging"], "MRI"],
    [["solar", "pv", "photovoltaic", "panel"], "Solar"],
    [["tractor", "harvester", "farm machine", "agri machine"], "Tractor"],
    [["agriculture", "agri", "crop", "seed", "fertilizer", "irrigation"], "Agriculture"],
    [["rice", "paddy", "grain"], "Rice"],
    [["shoe", "footwear", "boot", "sneaker", "sandal"], "Shoes"],
    [["electronic", "circuit", "pcb", "semiconductor", "component", "sensor"], "Electronics"],
    [["automobile", "car", "vehicle", "automotive", "truck", "motorbike"], "Automobile"],
    [["plant equipment", "pump", "compressor", "valve", "industrial"], "Plant Equipment"],
    [["machine", "machinery", "equipment", "tool", "cnc", "lathe"], "Machinery"],
    [["ai", "artificial intelligence", "robot", "automation", "ml"], "AI"],
  ];
  for (const [keywords, cat] of rules) {
    if (keywords.some((kw) => name.includes(kw))) return cat;
  }
  return "";
}

interface LightboxState {
  open: boolean;
  index: number;
}

export default function ExtractPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState<string>("");
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [selectedProducts, setSelectedProducts] = useState<Set<number>>(new Set());
  const [lightbox, setLightbox] = useState<LightboxState>({ open: false, index: 0 });
  const [expandedFeatures, setExpandedFeatures] = useState<Set<number>>(new Set());
  const [expandedSpecs, setExpandedSpecs] = useState<Set<number>>(new Set());
  
  // Product image lightbox state
  const [productImg, setProductImg] = useState<string | null>(null);

  // Editable Products State
  const [editableProducts, setEditableProducts] = useState<any[]>([]);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);

  const [productImages, setProductImages] = useState<Record<number, string[]>>({});
  const [armedPoolId, setArmedPoolId] = useState<string | null>(null);

  // Manual Cropping State
  const [manualImages, setManualImages] = useState<{ id: string, url: string }[]>([]);
  const [isCropMode, setIsCropMode] = useState(false);
  const [cropStart, setCropStart] = useState<{ x: number, y: number } | null>(null);
  const [cropEnd, setCropEnd] = useState<{ x: number, y: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isPolyCropMode, setIsPolyCropMode] = useState(false);
  const [polyPoints, setPolyPoints] = useState<{ x: number, y: number }[]>([]);
  const imageRef = useRef<HTMLImageElement>(null);

  const preview = usePreviewFile();
  const process = useProcessFile();
  const save = useSaveProducts();

  const assignedUrls = useMemo(
    () => new Set(Object.values(productImages).flat()),
    [productImages],
  );
  
  const poolItems = useMemo(
    () => [...(process.data?.imagePool ?? []), ...manualImages],
    [process.data, manualImages],
  );

  const assignImageToProduct = useCallback((productIndex: number, url: string) => {
    setProductImages((prev) => {
      const current = prev[productIndex] ?? [];
      if (current.length >= MAX_PRODUCT_IMAGES || current.includes(url)) return prev;
      return { ...prev, [productIndex]: [...current, url] };
    });
    setArmedPoolId(null);
  }, []);

  const removeImageFromProduct = useCallback((productIndex: number, imgIndex: number) => {
    setProductImages((prev) => {
      const current = prev[productIndex] ?? [];
      return { ...prev, [productIndex]: current.filter((_, i) => i !== imgIndex) };
    });
  }, []);

  const reorderProductImages = useCallback((productIndex: number, next: string[]) => {
    setProductImages((prev) => ({ ...prev, [productIndex]: next }));
  }, []);

  const handleSlotClick = useCallback((productIndex: number) => {
    if (!armedPoolId) {
      toast({ title: "Pick an image first", description: "Click a tile in the Image Pool below, then click this slot." });
      return;
    }
    const item = poolItems.find((p) => p.id === armedPoolId);
    if (item) assignImageToProduct(productIndex, item.url);
  }, [armedPoolId, poolItems, assignImageToProduct, toast]);

  // --- Background Removal Helper ---
  const handleRemoveBackground = async (imageSrc: string): Promise<string> => {
    try {
      toast({ title: "Removing background...", description: "Processing on the server." });
      const absoluteUrl = new URL(imageSrc, window.location.origin).href;
      const sourceBlob = await (await fetch(absoluteUrl)).blob();

      const formData = new FormData();
      formData.append("file", sourceBlob, "image.png");

      const response = await fetch("/api/remove-background", { method: "POST", body: formData });
      if (!response.ok) throw new Error("Server error");

      // The backend now saves the processed image to disk and returns its
      // permanent URL, instead of raw bytes for a throwaway blob: URL — see
      // handleApplyBackgroundRemoval for why that matters.
      const { url } = (await response.json()) as { url: string };
      toast({ title: "Success!", description: "Background removed successfully." });
      return url;
    } catch (error) {
      console.error("Background removal failed:", error);
      toast({ title: "Error", description: "Could not remove background.", variant: "destructive" });
      throw error;
    }
  };

  // Applies a background-removed image and makes it stick around:
  // - adds it to the Image Pool (so it's never lost, same as a crop)
  // - if the image being edited was already assigned to a product slot,
  //   swaps that slot to the new version in place
  // Previously this only called setProductImg(), which is just the
  // lightbox's preview state — closing the lightbox threw the result away.
  const handleApplyBackgroundRemoval = async () => {
    if (!productImg) return;
    const sourceUrl = productImg;
    try {
      const newTransparentUrl = await handleRemoveBackground(sourceUrl);

      const newId = `bg-removed-${Date.now()}`;
      setManualImages((prev) => [...prev, { id: newId, url: newTransparentUrl }]);

      setProductImages((prev) => {
        const next = { ...prev };
        for (const key of Object.keys(next)) {
          const idx = Number(key);
          if (next[idx]?.includes(sourceUrl)) {
            next[idx] = next[idx].map((u) => (u === sourceUrl ? newTransparentUrl : u));
          }
        }
        return next;
      });

      setProductImg(newTransparentUrl);
    } catch {
      // handled via toast inside handleRemoveBackground
    }
  };

  useEffect(() => {
    if (!lightbox.open || !preview.data || isCropMode) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox((s) => ({ ...s, open: false }));
      if (e.key === "ArrowRight") setLightbox((s) => ({ ...s, index: Math.min(s.index + 1, preview.data!.pages.length - 1) }));
      if (e.key === "ArrowLeft") setLightbox((s) => ({ ...s, index: Math.max(s.index - 1, 0) }));
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [lightbox.open, preview.data, isCropMode]);

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length === 0) return;
    const f = acceptedFiles[0]!;
    setFile(f);
    setCategory(detectCategory(f.name));
    preview.reset();
    process.reset();
    save.reset();
    setSelectedPages(new Set());
    setSelectedProducts(new Set());
    setEditableProducts([]);
    setEditingIndex(null);
    setProductImages({});
    setManualImages([]);
    setArmedPoolId(null);
  }, []);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      "application/pdf": [".pdf"],
      "image/jpeg": [".jpg", ".jpeg"],
      "image/png": [".png"],
    },
    maxFiles: 1,
  });

  const handlePreview = () => {
    if (!file) {
      toast({ title: "Error", description: "Please select a file first", variant: "destructive" });
      return;
    }
    preview.mutate({ data: { file } }, {
      onSuccess: (data) => {
        setSelectedPages(new Set(data.pages.map((p) => p.pageNumber)));
      },
      onError: (err) => {
        toast({ title: "Preview Failed", description: err.message || "Something went wrong", variant: "destructive" });
      },
    });
  };

  const togglePage = (pageNumber: number) => {
    setSelectedPages((prev) => {
      const next = new Set(prev);
      next.has(pageNumber) ? next.delete(pageNumber) : next.add(pageNumber);
      return next;
    });
  };

  const selectAll = () => {
    if (!preview.data) return;
    setSelectedPages(new Set(preview.data.pages.map((p) => p.pageNumber)));
  };

  const clearAll = () => setSelectedPages(new Set());

  const handleProcess = () => {
    if (!file || selectedPages.size === 0) return;
    const effectiveCategory = category || "General";
    process.mutate({
      data: { file, category: effectiveCategory, selectedPages: JSON.stringify(Array.from(selectedPages)) },
    }, {
      onSuccess: (data) => {
        setEditableProducts(data.products);
        setSelectedProducts(new Set(data.products.map((_, i) => i)));
        const imgMap: Record<number, string[]> = {};
        data.products.forEach((p, i) => {
          imgMap[i] = (p.images ?? []).slice(0, MAX_PRODUCT_IMAGES);
        });
        setProductImages(imgMap);
        setArmedPoolId(null);
      },
      onError: (err) => {
        toast({ title: "Processing Failed", description: err.message || "Something went wrong", variant: "destructive" });
      },
    });
  };

  const handleAddProduct = () => {
    const newProduct = {
      name: "New Custom Product",
      model: "",
      category: category || "General",
      description: "",
      price: "",
      features: [],
      specifications: {},
      pages: [],
      images: [],
    };
    
    setEditableProducts((prev) => {
      const next = [...prev, newProduct];
      setSelectedProducts((s) => new Set(s).add(next.length - 1));
      setEditingIndex(next.length - 1);
      return next;
    });
  };

  const handleDeleteProduct = (indexToDelete: number) => {
    setEditableProducts((prev) => prev.filter((_, idx) => idx !== indexToDelete));
    
    setSelectedProducts((prev) => {
      const next = new Set<number>();
      prev.forEach((idx) => {
        if (idx < indexToDelete) next.add(idx);
        else if (idx > indexToDelete) next.add(idx - 1);
      });
      return next;
    });

    setProductImages((prev) => {
      const next: Record<number, string[]> = {};
      Object.entries(prev).forEach(([keyStr, imgs]) => {
        const idx = Number(keyStr);
        if (idx < indexToDelete) {
          next[idx] = imgs;
        } else if (idx > indexToDelete) {
          next[idx - 1] = imgs;
        }
      });
      return next;
    });

    if (editingIndex === indexToDelete) {
      setEditingIndex(null);
    } else if (editingIndex !== null && editingIndex > indexToDelete) {
      setEditingIndex(editingIndex - 1);
    }

    toast({ description: "Product removed." });
  };

  const updateProductField = (index: number, field: string, value: any) => {
    setEditableProducts((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  };

  const toggleProduct = (index: number) => {
    setSelectedProducts((prev) => {
      const next = new Set(prev);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });
  };

  const handleSave = () => {
    if (!process.data || selectedProducts.size === 0) return;
    const productsToSave = Array.from(selectedProducts).map((i) => {
      const p = editableProducts[i]!;
      return {
        productName: p.name,
        category: p.category || category,
        model: p.model || null,
        description: p.description || "",
        price: p.price ?? null,
        features: p.features,
        specifications: p.specifications as Record<string, string>,
        images: (productImages[i] ?? p.images ?? []).slice(0, MAX_PRODUCT_IMAGES),
        sourceFileName: file?.name,
      };
    });
    
    save.mutate({
      data: { products: productsToSave, brochureId: process.data.brochureId },
    }, {
      onSuccess: () => {
        toast({ title: "Success", description: "Products saved to library" });
        queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
        queryClient.invalidateQueries({ queryKey: getListBrochuresQueryKey() });
        setFile(null);
        setCategory("");
        preview.reset();
        process.reset();
        setEditableProducts([]);
        setEditingIndex(null);
        setProductImages({});
        setManualImages([]);
        setArmedPoolId(null);
      },
      onError: (err) => {
        toast({ title: "Save Failed", description: err.message || "Something went wrong", variant: "destructive" });
      },
    });
  };

  const openLightbox = (index: number) => {
    setLightbox({ open: true, index });
    setIsCropMode(false);
    setCropStart(null);
    setCropEnd(null);
  };

  const handleCropMouseDown = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!isCropMode || !imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setCropStart({ x, y });
    setCropEnd({ x, y });
    setIsDragging(true);
  };

  const handleCropMouseMove = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!isCropMode || !isDragging || !cropStart || !imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
    const y = Math.max(0, Math.min(e.clientY - rect.top, rect.height));
    setCropEnd({ x, y });
  };

  const handleCropMouseUp = () => {
    setIsDragging(false);
  };

  const confirmCrop = () => {
    if (!cropStart || !cropEnd || !imageRef.current) return;
    
    const canvas = document.createElement('canvas');
    const scaleX = imageRef.current.naturalWidth / imageRef.current.width;
    const scaleY = imageRef.current.naturalHeight / imageRef.current.height;

    const x = Math.min(cropStart.x, cropEnd.x) * scaleX;
    const y = Math.min(cropStart.y, cropEnd.y) * scaleY;
    const width = Math.abs(cropEnd.x - cropStart.x) * scaleX;
    const height = Math.abs(cropEnd.y - cropStart.y) * scaleY;

    if (width < 10 || height < 10) {
      toast({ description: "Crop area too small.", variant: "destructive" });
      return;
    }

    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.drawImage(imageRef.current, x, y, width, height, 0, 0, width, height);
    
    const croppedUrl = canvas.toDataURL('image/jpeg');
    const newId = `manual-crop-${Date.now()}`;
    
    setManualImages(prev => [...prev, { id: newId, url: croppedUrl }]);
    toast({ title: "Image Cropped", description: "Added to the Image Pool." });
    
    setCropStart(null);
    setCropEnd(null);
    setIsCropMode(false);
  };

  const cancelCrop = () => {
    setCropStart(null);
    setCropEnd(null);
    setIsCropMode(false);
  };

  const handlePolyClick = (e: React.MouseEvent<HTMLImageElement>) => {
    if (!isPolyCropMode || !imageRef.current) return;
    const rect = imageRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;

    if (polyPoints.length > 2) {
      const first = polyPoints[0];
      const dist = Math.hypot(x - first.x, y - first.y);
      if (dist < 12) {
        confirmPolyCrop();
        return;
      }
    }
    setPolyPoints((prev) => [...prev, { x, y }]);
  };

  const confirmPolyCrop = () => {
    if (polyPoints.length < 3 || !imageRef.current) return;

    const scaleX = imageRef.current.naturalWidth / imageRef.current.width;
    const scaleY = imageRef.current.naturalHeight / imageRef.current.height;
    const scaledPoints = polyPoints.map(pt => ({ x: pt.x * scaleX, y: pt.y * scaleY }));

    const xs = scaledPoints.map(p => p.x);
    const ys = scaledPoints.map(p => p.y);
    const minX = Math.min(...xs), maxX = Math.max(...xs);
    const minY = Math.min(...ys), maxY = Math.max(...ys);
    const width = maxX - minX, height = maxY - minY;

    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.beginPath();
    scaledPoints.forEach((pt, i) => {
      const px = pt.x - minX, py = pt.y - minY;
      i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    });
    ctx.closePath();
    ctx.clip();

    ctx.drawImage(imageRef.current, minX, minY, width, height, 0, 0, width, height);

    const croppedUrl = canvas.toDataURL('image/png');
    setManualImages(prev => [...prev, { id: `manual-crop-${Date.now()}`, url: croppedUrl }]);
    toast({ title: "Image Cropped", description: "Added to the Image Pool." });
    setPolyPoints([]);
    setIsPolyCropMode(false);
  };

  const cancelPolyCrop = () => {
    setPolyPoints([]);
    setIsPolyCropMode(false);
  };

  const lbPage = preview.data?.pages[lightbox.index];
  const lbSelected = lbPage ? selectedPages.has(lbPage.pageNumber) : false;

  return (
    <div className="container py-8 max-w-6xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">

      <div className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight">Extract Data</h1>
        <p className="text-muted-foreground">Upload a catalog or brochure to digitize its products.</p>
      </div>

      {/* STEP 1: UPLOAD */}
      <Card className={`border-2 ${!preview.data && !process.data ? "border-primary" : "border-border"}`}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-lg">
            <span className="flex items-center justify-center w-6 h-6 rounded bg-primary text-primary-foreground text-xs font-mono">1</span>
            Source Document
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div
            {...getRootProps()}
            className={`border-2 border-dashed flex flex-col items-center justify-center p-8 text-center cursor-pointer transition-colors rounded
              ${isDragActive ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-primary/50"}
              ${file ? "bg-muted/30" : ""}
              ${preview.isPending || preview.data ? "pointer-events-none opacity-50" : ""}
            `}
          >
            <input {...getInputProps()} />
            {file ? (
              <div className="flex flex-col items-center gap-2">
                <div className="flex items-center gap-2 text-primary">
                  <FileText className="w-6 h-6" />
                  <span className="font-mono text-sm">{file.name}</span>
                  <span className="text-xs text-muted-foreground ml-2">({Math.round(file.size / 1024)} KB)</span>
                </div>
                {category && (
                  <Badge variant="secondary" className="font-mono mt-1">
                    Domain detected: {category}
                  </Badge>
                )}
                {!category && (
                  <p className="text-xs text-amber-600 mt-1">Category could not be auto-detected — it will be inferred from content.</p>
                )}
              </div>
            ) : (
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <UploadCloud className="w-10 h-10 mb-2" />
                <p className="text-sm font-medium">Drag & drop your brochure here</p>
                <p className="text-xs opacity-70">Supports PDF, JPG, PNG up to 50MB — category is detected automatically</p>
              </div>
            )}
          </div>
        </CardContent>
        {file && !preview.data && (
          <CardFooter className="bg-muted/20 border-t justify-end py-3">
            <Button onClick={handlePreview} disabled={preview.isPending}>
              {preview.isPending
                ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Generating Preview...</>
                : <><ChevronRight className="w-4 h-4 mr-2" />Preview Document</>}
            </Button>
          </CardFooter>
        )}
      </Card>

      {/* STEP 2: PAGE SELECTION */}
      {preview.data && !process.data && (
        <Card className="border-2 border-primary">
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <span className="flex items-center justify-center w-6 h-6 rounded bg-primary text-primary-foreground text-xs font-mono">2</span>
                  Select Pages to Process
                </CardTitle>
                <CardDescription className="mt-1">
                  {preview.data.totalPages} pages detected. Click a thumbnail to zoom in before selecting.
                </CardDescription>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={selectAll}
                  disabled={selectedPages.size === preview.data.totalPages}
                >
                  Select All
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={clearAll}
                  disabled={selectedPages.size === 0}
                >
                  Clear All
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
              {preview.data.pages.map((page, idx) => {
                const isSelected = selectedPages.has(page.pageNumber);
                return (
                  <div
                    key={page.pageNumber}
                    className={`relative group rounded overflow-hidden border-2 transition-all
                      ${isSelected ? "border-primary ring-2 ring-primary/20" : "border-transparent hover:border-primary/50 bg-muted/30"}`}
                  >
                    <img
                      src={page.imageUrl}
                      alt={`Page ${page.pageNumber}`}
                      onClick={() => togglePage(page.pageNumber)}
                      className={`w-full aspect-[1/1.4] object-cover object-top cursor-pointer transition-opacity
                        ${isSelected ? "opacity-100" : "opacity-70 group-hover:opacity-100"}`}
                      loading="lazy"
                    />

                    <div
                      className="absolute top-2 right-2 z-10 cursor-pointer"
                      onClick={() => togglePage(page.pageNumber)}
                    >
                      <div className={`w-5 h-5 rounded-sm border flex items-center justify-center
                        ${isSelected ? "bg-primary border-primary text-primary-foreground" : "bg-background/80 border-border"}`}>
                        {isSelected && <CheckCircle2 className="w-3.5 h-3.5" />}
                      </div>
                    </div>

                    <button
                      onClick={() => openLightbox(idx)}
                      className="absolute top-2 left-2 z-10 w-6 h-6 rounded bg-background/80 border border-border
                        flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-background"
                      title="View full size"
                    >
                      <Maximize2 className="w-3 h-3" />
                    </button>

                    <div className="absolute bottom-0 inset-x-0 bg-background/90 backdrop-blur-sm border-t p-1 text-center">
                      <span className="text-xs font-mono font-medium">PAGE {page.pageNumber}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          </CardContent>
          <CardFooter className="bg-muted/20 border-t flex items-center justify-between py-3">
            <div className="text-sm font-mono text-muted-foreground">
              {selectedPages.size} / {preview.data.totalPages} SELECTED
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => preview.reset()}>Cancel</Button>
              <Button onClick={handleProcess} disabled={process.isPending || selectedPages.size === 0}>
                {process.isPending
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Running OCR &amp; AI...</>
                  : <><DatabaseIcon className="w-4 h-4 mr-2" />Extract Product Data</>}
              </Button>
            </div>
          </CardFooter>
          {process.isPending && (
            <div className="px-6 pb-6 pt-0">
              <Progress value={undefined} className="h-1" />
              <p className="text-xs text-center text-muted-foreground mt-2 animate-pulse font-mono uppercase tracking-widest">
                Processing {selectedPages.size} pages. This may take a moment.
              </p>
            </div>
          )}
        </Card>
      )}

      {/* STEP 3: RESULTS & EDITING */}
      {process.data && (
        <Card className="border-2 border-primary">
          <CardHeader className="flex flex-row items-start justify-between space-y-0">
            <div>
              <CardTitle className="flex items-center gap-2 text-lg">
                <span className="flex items-center justify-center w-6 h-6 rounded bg-primary text-primary-foreground text-xs font-mono">3</span>
                Extracted Products
              </CardTitle>
              <CardDescription className="mt-1.5">
                Found {editableProducts.length} products. Verify, edit, and save to your library.
              </CardDescription>
            </div>
            <div className="flex items-center gap-3">
              <Button variant="outline" onClick={() => openLightbox(0)}>
                <Crop className="w-4 h-4 mr-2" /> Crop from Pages
              </Button>
              
              <Button variant="outline" onClick={handleAddProduct}>
                <Plus className="w-4 h-4 mr-2" /> Add Custom
              </Button>
              <Button onClick={handleSave} disabled={save.isPending || selectedProducts.size === 0}>
                {save.isPending
                  ? <><Loader2 className="w-4 h-4 mr-2 animate-spin" />Saving...</>
                  : <><Save className="w-4 h-4 mr-2" />Save {selectedProducts.size} Items</>}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 lg:grid-cols-[260px_1fr] gap-6 items-start">
              
              <div className="lg:sticky lg:top-4 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
                <ImagePool
                  items={poolItems}
                  armedId={armedPoolId}
                  onArm={setArmedPoolId}
                  onExpand={(url) => setProductImg(url)}
                  assignedUrls={assignedUrls}
                />
              </div>

              <div className="space-y-6 min-w-0">
                {editableProducts.length === 0 ? (
                  <div className="text-center py-12 text-muted-foreground">
                    No products found. Click "Add Custom" to create one manually.
                  </div>
                ) : (
                  editableProducts.map((product, i) => {
                    const isEditing = editingIndex === i;

                    return (
                      <div
                        key={i}
                        className={`flex gap-4 p-4 border rounded-lg transition-colors
                          ${selectedProducts.has(i) ? "bg-primary/5 border-primary/30" : "bg-card"}`}
                      >
                        <div className="pt-1 flex flex-col items-center gap-3">
                          <Checkbox
                            checked={selectedProducts.has(i)}
                            onCheckedChange={() => toggleProduct(i)}
                          />
                          <button
                            onClick={() => setEditingIndex(isEditing ? null : i)}
                            className="text-muted-foreground hover:text-primary transition-colors mt-2"
                            title={isEditing ? "Done Editing" : "Edit Product"}
                          >
                            {isEditing ? <CheckCircle2 className="w-5 h-5 text-primary" /> : <Edit2 className="w-4 h-4" />}
                          </button>
                          
                          <button
                            onClick={() => handleDeleteProduct(i)}
                            className="text-muted-foreground hover:text-destructive transition-colors mt-1"
                            title="Delete Product"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                        
                        <div className="w-40 shrink-0">
                          <ProductGallery
                            images={productImages[i] ?? product.images ?? []}
                            onReorder={(next) => reorderProductImages(i, next)}
                            onRemove={(imgIdx) => removeImageFromProduct(i, imgIdx)}
                            onSlotClick={() => handleSlotClick(i)}
                            slotsArmed={armedPoolId !== null}
                            onExpand={(url) => setProductImg(url)}
                          />
                        </div>
                        
                        <div className="flex-1 space-y-4">
                          {isEditing ? (
                            <div className="grid grid-cols-2 gap-3 mb-4">
                              <input 
                                className="col-span-2 text-xl font-bold border rounded p-2 bg-transparent"
                                value={product.name || ""}
                                onChange={(e) => updateProductField(i, "name", e.target.value)}
                                placeholder="Product Name"
                              />
                              <input 
                                className="border rounded p-2 text-sm bg-transparent"
                                value={product.category || ""}
                                onChange={(e) => updateProductField(i, "category", e.target.value)}
                                placeholder="Category"
                              />
                              <input 
                                className="border rounded p-2 text-sm bg-transparent"
                                value={product.model || ""}
                                onChange={(e) => updateProductField(i, "model", e.target.value)}
                                placeholder="Model"
                              />
                              <input 
                                className="border rounded p-2 text-sm bg-transparent"
                                value={product.price || ""}
                                onChange={(e) => updateProductField(i, "price", e.target.value)}
                                placeholder="Price"
                              />
                              <textarea 
                                className="col-span-2 border rounded p-2 text-sm bg-transparent h-16"
                                value={product.description || ""}
                                onChange={(e) => updateProductField(i, "description", e.target.value)}
                                placeholder="Product Description"
                              />
                            </div>
                          ) : (
                            <div>
                              <div className="flex items-center gap-2 mb-1">
                                <Badge variant="secondary" className="font-mono">{product.category || category}</Badge>
                                {product.model && <Badge variant="outline" className="font-mono">{product.model}</Badge>}
                                {product.price && (
                                  <Badge variant="outline" className="font-mono text-primary border-primary/40">
                                    {product.price}
                                  </Badge>
                                )}
                              </div>
                              <h3 className="text-xl font-bold">{product.name}</h3>
                              {product.description && (
                                <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{product.description}</p>
                              )}
                            </div>
                          )}

                          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            
                            <div>
                              <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Key Features</h4>
                              {isEditing ? (
                                <textarea 
                                  className="w-full border rounded p-2 text-sm bg-transparent h-32"
                                  placeholder="One feature per line"
                                  value={(product.features || []).join("\n")}
                                  onChange={(e) => updateProductField(i, "features", e.target.value.split("\n").filter(Boolean))}
                                />
                              ) : (
                                <>
                                  {product.features && product.features.length > 0 ? (
                                    <>
                                      <ul className="text-sm space-y-1 list-disc list-inside pl-4 marker:text-primary">
                                        {(expandedFeatures.has(i) ? product.features : product.features.slice(0, 5)).map((feat: string, fIdx: number) => (
                                          <li key={fIdx}>{feat}</li>
                                        ))}
                                      </ul>
                                      {product.features.length > 5 && (
                                        <button
                                          onClick={() => setExpandedFeatures(prev => {
                                            const next = new Set(prev);
                                            next.has(i) ? next.delete(i) : next.add(i);
                                            return next;
                                          })}
                                          className="mt-1.5 text-xs text-primary hover:underline font-mono"
                                        >
                                          {expandedFeatures.has(i) ? "Show less" : `+ ${product.features.length - 5} more features`}
                                        </button>
                                      )}
                                    </>
                                  ) : (
                                    <p className="text-xs text-muted-foreground">No features listed.</p>
                                  )}
                                </>
                              )}
                            </div>

                            <div>
                              <h4 className="text-xs font-bold uppercase tracking-wider text-muted-foreground mb-2">Specifications</h4>
                              {isEditing ? (
                                <textarea 
                                  className="w-full border rounded p-2 text-sm bg-transparent h-32"
                                  placeholder="Key: Value (one per line)"
                                  value={Object.entries(product.specifications || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}
                                  onChange={(e) => {
                                    const lines = e.target.value.split("\n");
                                    const newSpecs: Record<string, string> = {};
                                    lines.forEach((l) => {
                                      const parts = l.split(":");
                                      const key = parts[0]?.trim();
                                      const val = parts.slice(1).join(":").trim();
                                      if (key) newSpecs[key] = val;
                                    });
                                    updateProductField(i, "specifications", newSpecs);
                                  }}
                                />
                              ) : (
                                <>
                                  {product.specifications && Object.keys(product.specifications).length > 0 ? (
                                    <>
                                      <div className="text-sm border-y divide-y">
                                        {(expandedSpecs.has(i)
                                          ? Object.entries(product.specifications)
                                          : Object.entries(product.specifications).slice(0, 5)
                                        ).map(([key, val]) => (
                                          <div key={key} className="flex py-1.5 gap-2">
                                            <span className="font-medium min-w-[100px] text-muted-foreground">{key}</span>
                                            <span>{String(val)}</span>
                                          </div>
                                        ))}
                                      </div>
                                      {Object.keys(product.specifications).length > 5 && (
                                        <button
                                          onClick={() => setExpandedSpecs(prev => {
                                            const next = new Set(prev);
                                            next.has(i) ? next.delete(i) : next.add(i);
                                            return next;
                                          })}
                                          className="mt-1.5 text-xs text-primary hover:underline font-mono"
                                        >
                                          {expandedSpecs.has(i) ? "Show less" : `+ ${Object.keys(product.specifications).length - 5} more specs`}
                                        </button>
                                      )}
                                    </>
                                  ) : (
                                    <p className="text-xs text-muted-foreground">No specifications listed.</p>
                                  )}
                                </>
                              )}
                            </div>

                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* PAGE LIGHTBOX */}
      {lightbox.open && preview.data && lbPage && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex flex-col"
          onMouseUp={handleCropMouseUp}
        >
          <div className="flex items-center justify-between px-4 py-3 bg-black/60 shrink-0">
            <span className="text-white font-mono text-sm">
              PAGE {lbPage.pageNumber} / {preview.data.totalPages}
            </span>
            <div className="flex items-center gap-3">
              <button
                onClick={() => isCropMode ? cancelCrop() : setIsCropMode(true)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded border text-sm font-mono transition-colors
                  ${isCropMode
                    ? "bg-amber-500 border-amber-500 text-white"
                    : "border-white/40 text-white hover:border-white"}`}
              >
                <Crop className="w-4 h-4" /> {isCropMode ? "Cancel Crop" : "Crop Area"}
              </button>

              <button
                onClick={() => isPolyCropMode ? cancelPolyCrop() : setIsPolyCropMode(true)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded border text-sm font-mono transition-colors
                  ${isPolyCropMode
                    ? "bg-amber-500 border-amber-500 text-white"
                    : "border-white/40 text-white hover:border-white"}`}
              >
                <Crop className="w-4 h-4" /> {isPolyCropMode ? "Cancel Shape" : "Crop Shape"}
              </button>

              <button
                onClick={() => togglePage(lbPage.pageNumber)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded border text-sm font-mono transition-colors
                  ${lbSelected
                    ? "bg-primary border-primary text-primary-foreground"
                    : "border-white/40 text-white hover:border-white"}`}
                disabled={isCropMode}
              >
                {lbSelected
                  ? <><CheckCircle2 className="w-4 h-4" />Selected</>
                  : <>Select this page</>}
              </button>

              <button
                onClick={() => setLightbox((s) => ({ ...s, open: false }))}
                className="w-8 h-8 flex items-center justify-center rounded text-white/70 hover:text-white hover:bg-white/10"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          <div className="flex-1 flex items-center justify-center overflow-hidden relative min-h-0">
            {lightbox.index > 0 && !isCropMode && (
              <button
                onClick={() => setLightbox((s) => ({ ...s, index: s.index - 1 }))}
                className="absolute left-4 z-10 w-10 h-10 flex items-center justify-center rounded-full bg-black/50 text-white hover:bg-black/80 transition-colors"
              >
                <ChevronLeft className="w-6 h-6" />
              </button>
            )}

            <div className="relative inline-block h-full flex items-center justify-center">
              <img
                ref={imageRef}
                src={lbPage.imageUrl}
                alt={`Page ${lbPage.pageNumber}`}
                className={`max-h-full max-w-full object-contain select-none ${isCropMode || isPolyCropMode ? "cursor-crosshair" : ""}`}
                draggable={false}
                onMouseDown={handleCropMouseDown}
                onMouseMove={handleCropMouseMove}
                onClick={handlePolyClick}
              />
              
              {isCropMode && cropStart && cropEnd && (
                <div 
                  className="absolute border-2 border-primary bg-primary/20 pointer-events-none"
                  style={{
                    left: Math.min(cropStart.x, cropEnd.x),
                    top: Math.min(cropStart.y, cropEnd.y),
                    width: Math.abs(cropEnd.x - cropStart.x),
                    height: Math.abs(cropEnd.y - cropStart.y),
                  }}
                />
              )}

              {isPolyCropMode && polyPoints.length > 0 && (
                <svg className="absolute inset-0 pointer-events-none" style={{ width: '100%', height: '100%' }}>
                  <polyline
                    points={polyPoints.map(p => `${p.x},${p.y}`).join(' ')}
                    fill="rgba(59,130,246,0.15)"
                    stroke="rgb(59,130,246)"
                    strokeWidth={2}
                  />
                  {polyPoints.map((pt, i) => (
                    <circle key={i} cx={pt.x} cy={pt.y} r={5} fill={i === 0 ? "orange" : "rgb(59,130,246)"} />
                  ))}
                </svg>
              )}

              {isCropMode && cropStart && cropEnd && !isDragging && Math.abs(cropEnd.x - cropStart.x) > 10 && (
                <button
                  onClick={confirmCrop}
                  className="absolute z-20 px-3 py-1 bg-primary text-primary-foreground font-mono text-sm rounded shadow-lg transform -translate-x-1/2 -translate-y-1/2"
                  style={{
                    left: Math.max(cropStart.x, cropEnd.x) - (Math.abs(cropEnd.x - cropStart.x) / 2),
                    top: Math.max(cropStart.y, cropEnd.y) + 20,
                  }}
                >
                  Save Selection to Pool
                </button>
              )}
            </div>

            {lightbox.index < preview.data.pages.length - 1 && !isCropMode && (
              <button
                onClick={() => setLightbox((s) => ({ ...s, index: s.index + 1 }))}
                className="absolute right-4 z-10 w-10 h-10 flex items-center justify-center rounded-full bg-black/50 text-white hover:bg-black/80 transition-colors"
              >
                <ChevronRightIcon className="w-6 h-6" />
              </button>
            )}
          </div>

          <div className="shrink-0 flex gap-2 overflow-x-auto px-4 py-3 bg-black/60">
            {preview.data.pages.map((p, idx) => (
              <button
                key={p.pageNumber}
                onClick={() => {
                  setLightbox((s) => ({ ...s, index: idx }));
                  setIsCropMode(false);
                  setCropStart(null);
                  setCropEnd(null);
                }}
                className={`shrink-0 w-14 rounded overflow-hidden border-2 transition-all
                  ${idx === lightbox.index ? "border-primary" : "border-transparent opacity-50 hover:opacity-80"}`}
              >
                <img src={p.imageUrl} alt="" className="w-full aspect-[1/1.4] object-cover object-top" />
              </button>
            ))}
          </div>
        </div>
      )}

      {/* PRODUCT IMAGE LIGHTBOX WITH CROP & BACKGROUND REMOVAL */}
      {productImg && (
        <div
          className="fixed inset-0 z-50 bg-black/90 flex flex-col"
          onMouseUp={handleCropMouseUp}
        >
          <div className="flex items-center justify-between px-4 py-3 bg-black/60 shrink-0">
            <span className="text-white font-mono text-sm">Product Image</span>
            <div className="flex items-center gap-3">
              {/* ✨ REMOVE BACKGROUND BUTTON */}
              <button
                onClick={handleApplyBackgroundRemoval}
                className="flex items-center gap-2 px-3 py-1.5 rounded border border-white/40 text-white text-sm font-mono hover:border-white transition-colors"
                title="Remove background and save"
              >
                ✨ Remove Background
              </button>

              <button
                onClick={() => isCropMode ? cancelCrop() : setIsCropMode(true)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded border text-sm font-mono transition-colors
                  ${isCropMode
                    ? "bg-amber-500 border-amber-500 text-white"
                    : "border-white/40 text-white hover:border-white"}`}
              >
                <Crop className="w-4 h-4" /> {isCropMode ? "Cancel Crop" : "Crop Area"}
              </button>

              <button
                onClick={() => isPolyCropMode ? cancelPolyCrop() : setIsPolyCropMode(true)}
                className={`flex items-center gap-2 px-3 py-1.5 rounded border text-sm font-mono transition-colors
                  ${isPolyCropMode
                    ? "bg-amber-500 border-amber-500 text-white"
                    : "border-white/40 text-white hover:border-white"}`}
              >
                <Crop className="w-4 h-4" /> {isPolyCropMode ? "Cancel Shape" : "Crop Shape"}
              </button>
              <button
                className="w-8 h-8 flex items-center justify-center rounded text-white/70 hover:text-white hover:bg-white/10"
                onClick={() => { 
                  setProductImg(null); 
                  setIsCropMode(false); 
                  setCropStart(null); 
                  setCropEnd(null); 
                  setIsPolyCropMode(false);
                  setPolyPoints([]);
                }}
              >
                <X className="w-5 h-5" />
              </button>
            </div>
          </div>

          <div className="flex-1 flex items-center justify-center overflow-hidden relative min-h-0">
            <div className="relative inline-block h-full flex items-center justify-center">
              <img
                ref={imageRef}
                src={productImg}
                alt=""
                className={`max-h-full max-w-full object-contain shadow-2xl ${isCropMode || isPolyCropMode ? "cursor-crosshair" : ""}`}
                draggable={false}
                onMouseDown={handleCropMouseDown}
                onMouseMove={handleCropMouseMove}
                onClick={handlePolyClick}
              />
              
              {isCropMode && cropStart && cropEnd && (
                <div 
                  className="absolute border-2 border-primary bg-primary/20 pointer-events-none"
                  style={{
                    left: Math.min(cropStart.x, cropEnd.x),
                    top: Math.min(cropStart.y, cropEnd.y),
                    width: Math.abs(cropEnd.x - cropStart.x),
                    height: Math.abs(cropEnd.y - cropStart.y),
                  }}
                />
              )}

              {isPolyCropMode && polyPoints.length > 0 && (
                <svg className="absolute inset-0 pointer-events-none" style={{ width: '100%', height: '100%' }}>
                  <polyline
                    points={polyPoints.map(p => `${p.x},${p.y}`).join(' ')}
                    fill="rgba(59,130,246,0.15)"
                    stroke="rgb(59,130,246)"
                    strokeWidth={2}
                  />
                  {polyPoints.map((pt, i) => (
                    <circle key={i} cx={pt.x} cy={pt.y} r={5} fill={i === 0 ? "orange" : "rgb(59,130,246)"} />
                  ))}
                </svg>
              )}

              {isCropMode && cropStart && cropEnd && !isDragging && Math.abs(cropEnd.x - cropStart.x) > 10 && (
                <button
                  onClick={confirmCrop}
                  className="absolute z-20 px-3 py-1 bg-primary text-primary-foreground font-mono text-sm rounded shadow-lg transform -translate-x-1/2 -translate-y-1/2"
                  style={{
                    left: Math.max(cropStart.x, cropEnd.x) - (Math.abs(cropEnd.x - cropStart.x) / 2),
                    top: Math.max(cropStart.y, cropEnd.y) + 20,
                  }}
                >
                  Save Selection to Pool
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DatabaseIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg {...props} xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <ellipse cx="12" cy="5" rx="9" ry="3"/>
      <path d="M3 5V19A9 3 0 0 0 21 19V5"/>
      <path d="M3 12A9 3 0 0 0 21 12"/>
    </svg>
  );
}
import { useEffect, useState } from "react";
import { ImageOff, Maximize2, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";

export const MAX_PRODUCT_IMAGES = 5;

interface ProductGalleryProps {
  /** Ordered image URLs. images[0] is always the "main" image. */
  images: string[];
  /** Called with the reordered array whenever the user clicks a thumbnail to promote it to main. */
  onReorder?: (next: string[]) => void;
  /** Called with the index to remove — pass to allow un-assigning an image back to a pool. Omit to hide the remove control. */
  onRemove?: (index: number) => void;
  /** Called when an empty slot (below MAX_PRODUCT_IMAGES) is clicked — pass to enable "assign from pool" affordance. */
  onSlotClick?: () => void;
  /** Whether an image is currently "armed" for assignment from an external pool — highlights empty slots as drop targets. */
  slotsArmed?: boolean;
  onExpand?: (url: string) => void;
  className?: string;
}

/**
 * One large "hero" image with a filmstrip of thumbnails underneath. Clicking a
 * thumbnail promotes it to the hero position — the previous hero slides down
 * into the strip. Fully self-contained (uncontrolled) unless `onReorder` is
 * supplied, in which case the parent is also notified so it can persist order.
 */
export function ProductGallery({
  images,
  onReorder,
  onRemove,
  onSlotClick,
  slotsArmed,
  onExpand,
  className,
}: ProductGalleryProps) {
  const [order, setOrder] = useState(images);

  // Resync when the underlying image set changes (new assignment, removal, etc).
  useEffect(() => {
    setOrder(images);
  }, [images]);

  const promote = (index: number) => {
    if (index === 0) return;
    const next = [order[index]!, ...order.slice(0, index), ...order.slice(index + 1)];
    setOrder(next);
    onReorder?.(next);
  };

  const emptySlotCount = Math.max(0, MAX_PRODUCT_IMAGES - order.length);
  const main = order[0];

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {/* HERO */}
      <div className="relative aspect-[4/3] rounded-lg overflow-hidden border bg-white group">
        {main ? (
          <button
            type="button"
            onClick={() => onExpand?.(main)}
            className="w-full h-full block"
            title="Click to enlarge"
          >
            <img
              src={main}
              alt=""
              className="w-full h-full object-contain mix-blend-multiply transition-transform duration-300 group-hover:scale-[1.03]"
            />
            {onExpand && (
              <span className="absolute inset-0 flex items-center justify-center bg-black/0 group-hover:bg-black/20 transition-colors">
                <Maximize2 className="text-white opacity-0 group-hover:opacity-100 transition-opacity w-5 h-5" />
              </span>
            )}
          </button>
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-muted-foreground/50">
            <ImageOff className="w-8 h-8" />
            <span className="text-[10px] font-mono uppercase tracking-wider">No image</span>
          </div>
        )}

        {onRemove && main && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onRemove(0);
            }}
            className="absolute top-1.5 right-1.5 w-6 h-6 rounded-full bg-black/60 text-white
              flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity hover:bg-black/80"
            title="Remove image"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}

        {order.length > 1 && (
          <span className="absolute bottom-1.5 left-1.5 text-[10px] font-mono px-1.5 py-0.5 rounded bg-black/60 text-white">
            1 / {order.length}
          </span>
        )}
      </div>

      {/* FILMSTRIP */}
      {(order.length > 1 || emptySlotCount > 0) && (
        <div className="flex flex-wrap gap-1.5 w-full">
          {order.slice(1).map((url, i) => {
            const realIndex = i + 1;
            return (
              <div key={`${url}-${realIndex}`} className="relative group/thumb shrink-0">
                <button
                  type="button"
                  onClick={() => promote(realIndex)}
                  className="w-12 h-12 rounded border overflow-hidden bg-white block
                    hover:ring-2 hover:ring-primary/50 transition-all"
                  title="Click to set as main image"
                >
                  <img src={url} alt="" className="w-full h-full object-contain mix-blend-multiply" />
                </button>
                {onRemove && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onRemove(realIndex);
                    }}
                    className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-destructive text-destructive-foreground
                      flex items-center justify-center opacity-0 group-hover/thumb:opacity-100 transition-opacity"
                    title="Remove"
                  >
                    <X className="w-2.5 h-2.5" />
                  </button>
                )}
              </div>
            );
          })}

          {onSlotClick &&
            Array.from({ length: emptySlotCount }).map((_, i) => (
              <button
                key={`slot-${i}`}
                type="button"
                onClick={onSlotClick}
                className={cn(
                  "w-12 h-12 rounded border border-dashed shrink-0 flex items-center justify-center transition-all",
                  slotsArmed
                    ? "border-primary bg-primary/10 text-primary animate-pulse"
                    : "border-muted-foreground/30 text-muted-foreground/40 hover:border-primary/50 hover:text-primary/60",
                )}
                title={slotsArmed ? "Click to place selected image here" : "Select an image from the pool below, then click here"}
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
            ))}
        </div>
      )}
    </div>
  );
}

import { ImagesIcon, MousePointerClick, ZoomIn, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ImagePoolItem } from "@workspace/api-client-react";

interface ImagePoolProps {
  items: ImagePoolItem[] | { id: string, url: string, pageNumber?: number, source?: string }[];
  armedId: string | null;
  onArm: (id: string | null) => void;
  onExpand?: (url: string) => void;
  assignedUrls?: Set<string>; // Added to track assigned status
}

/**
 * Global pool of every isolated image pulled from the brochure or manually cropped.
 * Click a tile to "arm" it (it lifts up and glows), then click any open slot on a 
 * product's gallery to drop it there. Click the armed tile again — or any other 
 * tile — to change your mind.
 */
export function ImagePool({ items, armedId, onArm, onExpand, assignedUrls }: ImagePoolProps) {
  return (
    <div className="rounded-lg border-2 border-dashed border-muted-foreground/25 bg-muted/10 p-4">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <ImagesIcon className="w-4 h-4 text-muted-foreground" />
          <h4 className="text-sm font-bold">Image Pool</h4>
          <span className="text-xs font-mono text-muted-foreground">
            {items.length} total
          </span>
        </div>
        {armedId && (
          <div className="flex items-center gap-1.5 text-xs text-primary font-medium animate-pulse">
            <MousePointerClick className="w-3.5 h-3.5" />
            Pick a slot on any product below
          </div>
        )}
      </div>

      {items.length === 0 ? (
        <p className="text-xs text-muted-foreground text-center py-6">
          No images available in the pool.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {items.map((item) => {
            const isArmed = armedId === item.id;
            const isAssigned = assignedUrls?.has(item.url);

            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onArm(isArmed ? null : item.id)}
                className={cn(
                  "group relative w-16 h-16 rounded-md border-2 overflow-hidden bg-white shrink-0 transition-all duration-150",
                  isArmed
                    ? "border-primary -translate-y-1.5 shadow-lg shadow-primary/30 ring-2 ring-primary/30"
                    : "border-border hover:border-primary/50 hover:-translate-y-0.5",
                )}
                title={`Page ${item.pageNumber || 'Crop'} · ${item.source === "embedded" ? "embedded photo" : "full page"}${isArmed ? " · click a slot to place" : " · click to pick"}`}
              >
                <img src={item.url} alt="" className="w-full h-full object-contain mix-blend-multiply" />
                
                {/* VISUAL MARKER: Shows if the image is already used */}
                {isAssigned && (
                  <span 
                    className="absolute top-1 left-1 bg-green-500 text-white rounded-full p-0.5 shadow-sm"
                    title="Assigned to a product"
                  >
                    <CheckCircle2 className="w-2.5 h-2.5" />
                  </span>
                )}

                {onExpand && (
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => {
                      e.stopPropagation();
                      onExpand(item.url);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.stopPropagation();
                        e.preventDefault();
                        onExpand(item.url);
                      }
                    }}
                    className="absolute top-1 right-1 p-1 rounded bg-background/80 text-muted-foreground opacity-0 group-hover:opacity-100 focus:opacity-100 hover:!bg-background hover:text-foreground transition-opacity"
                    title="View larger"
                  >
                    <ZoomIn className="w-3 h-3" />
                  </span>
                )}
                <span
                  className={cn(
                    "absolute bottom-0 inset-x-0 text-center text-[9px] font-mono py-0.5",
                    isArmed ? "bg-primary text-primary-foreground" : "bg-background/80 text-muted-foreground",
                  )}
                >
                  {item.pageNumber ? `P${item.pageNumber}` : 'CROP'}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
import { useListBrochures, useDeleteBrochure, getListBrochuresQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { FileText, Trash2, Calendar, Database, ImageIcon } from "lucide-react";
import { format } from "date-fns";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";

export default function HistoryPage() {
  const { data: brochures, isLoading } = useListBrochures();
  const deleteMutation = useDeleteBrochure();
  const queryClient = useQueryClient();
  const { toast } = useToast();

  const handleDelete = (id: string) => {
    if (!confirm("Delete this brochure record?")) return;
    
    deleteMutation.mutate({ id }, {
      onSuccess: () => {
        toast({ title: "Brochure deleted" });
        queryClient.invalidateQueries({ queryKey: getListBrochuresQueryKey() });
      },
      onError: (err) => {
        toast({ title: "Failed to delete", description: err.message, variant: "destructive" });
      }
    });
  };

  return (
    <div className="container py-8 max-w-5xl mx-auto space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Processing History</h1>
        <p className="text-muted-foreground">Log of all previously analyzed catalogs and brochures.</p>
      </div>

      {isLoading ? (
        <div className="border rounded-lg">
          <div className="p-4 border-b bg-muted/50"><Skeleton className="h-6 w-48" /></div>
          <div className="p-4 space-y-4">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        </div>
      ) : !brochures || brochures.length === 0 ? (
        <div className="text-center py-24 border-2 border-dashed rounded-lg bg-muted/10">
          <FileText className="mx-auto h-12 w-12 text-muted-foreground mb-4 opacity-50" />
          <h3 className="text-lg font-bold mb-1">No history</h3>
          <p className="text-sm text-muted-foreground">
            Processed documents will appear here.
          </p>
        </div>
      ) : (
        <div className="border rounded-lg overflow-hidden bg-card">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[300px]">File Name</TableHead>
                <TableHead>Category</TableHead>
                <TableHead>Summary</TableHead>
                <TableHead>Date Processed</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {brochures.map((brochure) => (
                <TableRow key={brochure._id}>
                  <TableCell className="font-medium">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 text-primary" />
                      <span className="truncate max-w-[250px]" title={brochure.fileName}>
                        {brochure.fileName}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline" className="font-mono text-[10px]">
                      {brochure.category}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-4 text-xs text-muted-foreground">
                      {brochure.specifications && brochure.specifications.length > 0 && (
                        <div className="flex items-center gap-1" title="Extracted specs">
                          <Database className="h-3.5 w-3.5" />
                          <span>{brochure.specifications.length}</span>
                        </div>
                      )}
                      {brochure.extractedImages && brochure.extractedImages.length > 0 && (
                        <div className="flex items-center gap-1" title="Extracted images">
                          <ImageIcon className="h-3.5 w-3.5" />
                          <span>{brochure.extractedImages.length}</span>
                        </div>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm font-mono">
                    <div className="flex items-center gap-2">
                      <Calendar className="h-3 w-3" />
                      {format(new Date(brochure.uploadDate), "MMM d, yyyy HH:mm")}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className="h-8 text-muted-foreground hover:text-destructive"
                      onClick={() => handleDelete(brochure._id)}
                      disabled={deleteMutation.isPending}
                    >
                      <Trash2 className="h-4 w-4" />
                      <span className="sr-only">Delete</span>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

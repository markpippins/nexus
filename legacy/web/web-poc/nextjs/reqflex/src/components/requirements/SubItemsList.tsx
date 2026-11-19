'use client';

import { useState, useTransition } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import type { Requirement, SubItem, Status } from '@/lib/types';
import { addSubItem, updateSubItem, deleteSubItem } from '@/lib/actions';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { StatusBadge } from './StatusBadge';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '../ui/alert-dialog';

const SubItemSchema = z.object({
  name: z.string().min(3, 'Name must be at least 3 characters.'),
  status: z.enum(['pending', 'in-progress', 'complete']),
});

type SubItemFormValues = z.infer<typeof SubItemSchema>;

function SubItemForm({
  requirementId,
  subItem,
  setDialogOpen,
}: {
  requirementId: string;
  subItem?: SubItem;
  setDialogOpen: (open: boolean) => void;
}) {
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();

  const form = useForm<SubItemFormValues>({
    resolver: zodResolver(SubItemSchema),
    defaultValues: {
      name: subItem?.name || '',
      status: subItem?.status || 'pending',
    },
  });

  const onSubmit = (values: SubItemFormValues) => {
    startTransition(async () => {
      const formData = new FormData();
      Object.entries(values).forEach(([key, value]) => formData.append(key, value));
      
      const action = subItem
        ? updateSubItem.bind(null, requirementId, subItem.id)
        : addSubItem.bind(null, requirementId);
      
      const result = await action(formData);

      if (result?.errors) {
        toast({ variant: 'destructive', title: 'Error saving sub-item.' });
      } else {
        toast({ title: `Sub-item ${subItem ? 'updated' : 'added'}.` });
        setDialogOpen(false);
      }
    });
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input placeholder="e.g., Design database schema" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="status"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Status</FormLabel>
              <Select onValueChange={field.onChange} defaultValue={field.value}>
                <FormControl>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                </FormControl>
                <SelectContent>
                  <SelectItem value="pending">Pending</SelectItem>
                  <SelectItem value="in-progress">In Progress</SelectItem>
                  <SelectItem value="complete">Complete</SelectItem>
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline">Cancel</Button>
          </DialogClose>
          <Button type="submit" disabled={isPending}>{isPending ? 'Saving...' : 'Save'}</Button>
        </DialogFooter>
      </form>
    </Form>
  );
}

export function SubItemsList({ requirement }: { requirement: Requirement }) {
  const [dialogState, setDialogState] = useState<{ open: boolean; subItem?: SubItem }>({ open: false });
  const { toast } = useToast();
  const [isPending, startTransition] = useTransition();
  
  const handleDelete = (subItemId: string) => {
    startTransition(async () => {
      await deleteSubItem(requirement.id, subItemId);
      toast({ title: 'Sub-item deleted' });
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Dialog open={dialogState.open && !dialogState.subItem} onOpenChange={(open) => setDialogState({ open, subItem: undefined })}>
          <DialogTrigger asChild>
            <Button variant="outline"><Plus className="mr-2 h-4 w-4" /> Add Sub-item</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader><DialogTitle>Add New Sub-item</DialogTitle></DialogHeader>
            <SubItemForm
              requirementId={requirement.id}
              setDialogOpen={(open) => setDialogState({ open })}
            />
          </DialogContent>
        </Dialog>
      </div>

      <div className="rounded-lg border">
        {requirement.subItems.length > 0 ? (
          <ul className="divide-y">
            {requirement.subItems.map(subItem => (
              <li key={subItem.id} className="flex items-center justify-between p-3">
                <div className="flex items-center gap-4">
                  <StatusBadge status={subItem.status} />
                  <span className="font-medium">{subItem.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <Dialog open={dialogState.open && dialogState.subItem?.id === subItem.id} onOpenChange={(open) => setDialogState({ open, subItem: open ? subItem : undefined })}>
                    <DialogTrigger asChild>
                      <Button variant="ghost" size="icon" className="h-8 w-8"><Pencil className="h-4 w-4" /></Button>
                    </DialogTrigger>
                    <DialogContent>
                      <DialogHeader><DialogTitle>Edit Sub-item</DialogTitle></DialogHeader>
                      <SubItemForm
                        requirementId={requirement.id}
                        subItem={subItem}
                        setDialogOpen={(open) => setDialogState({ open, subItem: open ? subItem : undefined })}
                      />
                    </DialogContent>
                  </Dialog>
                  
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                       <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive hover:text-destructive"><Trash2 className="h-4 w-4" /></Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Are you sure?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This action cannot be undone. This will permanently delete the sub-item "{subItem.name}".
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={() => handleDelete(subItem.id)} disabled={isPending}>
                          {isPending ? 'Deleting...' : 'Delete'}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="p-8 text-center text-muted-foreground">
            No sub-items have been added yet.
          </div>
        )}
      </div>
    </div>
  );
}

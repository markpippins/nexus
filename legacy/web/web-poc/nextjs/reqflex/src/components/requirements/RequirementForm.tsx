'use client';

import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { createRequirement, updateRequirement } from '@/lib/actions';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import type { Requirement, Status } from '@/lib/types';
import { useRouter } from 'next/navigation';
import { useTransition } from 'react';

const RequirementSchema = z.object({
  name: z.string().min(3, 'Name must be at least 3 characters.'),
  description: z.string().min(10, 'Description must be at least 10 characters.'),
  technologies: z.string().min(1, 'Please add at least one technology.'),
  status: z.enum(['pending', 'in-progress', 'complete']),
});

type RequirementFormValues = z.infer<typeof RequirementSchema>;

interface RequirementFormProps {
  requirement?: Requirement;
  setSheetOpen?: (open: boolean) => void;
}

export function RequirementForm({ requirement, setSheetOpen }: RequirementFormProps) {
  const { toast } = useToast();
  const router = useRouter();
  const [isPending, startTransition] = useTransition();

  const form = useForm<RequirementFormValues>({
    resolver: zodResolver(RequirementSchema),
    defaultValues: {
      name: requirement?.name || '',
      description: requirement?.description || '',
      technologies: requirement?.technologies.join(', ') || '',
      status: requirement?.status || 'pending',
    },
  });

  const onSubmit = (values: RequirementFormValues) => {
    startTransition(async () => {
      const formData = new FormData();
      Object.entries(values).forEach(([key, value]) => {
        formData.append(key, value);
      });

      const action = requirement
        ? updateRequirement.bind(null, requirement.id)
        : createRequirement;

      const result = await action(formData);

      if (result?.errors) {
        toast({
          variant: 'destructive',
          title: 'Error submitting form',
          description: 'Please check the fields and try again.',
        });
      } else {
        toast({
          title: `Requirement ${requirement ? 'updated' : 'created'}`,
          description: `"${values.name}" has been successfully saved.`,
        });
        setSheetOpen?.(false);
        // a bit of a hack to make sure data is fresh
        router.refresh()
      }
    });
  };

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6 pt-6">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Requirement Name</FormLabel>
              <FormControl>
                <Input placeholder="e.g., User Authentication API" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="description"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Description</FormLabel>
              <FormControl>
                <Textarea
                  placeholder="Describe the requirement in detail."
                  className="min-h-[120px]"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="technologies"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Technologies</FormLabel>
              <FormControl>
                <Input placeholder="Go, React, PostgreSQL" {...field} />
              </FormControl>
              <FormDescription>
                Comma-separated list of technologies.
              </FormDescription>
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
                  <SelectTrigger>
                    <SelectValue placeholder="Select a status" />
                  </SelectTrigger>
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
        <div className="flex justify-end gap-4">
            <Button type="button" variant="outline" onClick={() => setSheetOpen?.(false)}>Cancel</Button>
            <Button type="submit" disabled={isPending}>
              {isPending ? 'Saving...' : 'Save Requirement'}
            </Button>
        </div>
      </form>
    </Form>
  );
}

'use client';

import { useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Plus } from 'lucide-react';
import type { Requirement, Status } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { RequirementCard } from './RequirementCard';
import { RequirementForm } from './RequirementForm';

const statuses: Status[] = ['pending', 'in-progress', 'complete'];

export function RequirementDashboard({ initialRequirements }: { initialRequirements: Requirement[] }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isSheetOpen, setSheetOpen] = useState(false);

  const currentStatus = searchParams.get('status') || 'all';

  const handleTabChange = (value: string) => {
    const params = new URLSearchParams(searchParams);
    if (value === 'all') {
      params.delete('status');
    } else {
      params.set('status', value);
    }
    router.push(`?${params.toString()}`);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-bold tracking-tight font-headline">Requirements</h1>
        <div className="flex items-center gap-4">
          <Tabs value={currentStatus} onValueChange={handleTabChange}>
            <TabsList>
              <TabsTrigger value="all">All</TabsTrigger>
              {statuses.map(status => (
                <TabsTrigger key={status} value={status} className="capitalize">
                  {status.replace('-', ' ')}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <Sheet open={isSheetOpen} onOpenChange={setSheetOpen}>
            <SheetTrigger asChild>
              <Button>
                <Plus className="mr-2 h-4 w-4" />
                New Requirement
              </Button>
            </SheetTrigger>
            <SheetContent className="sm:max-w-2xl w-full">
              <SheetHeader>
                <SheetTitle>Create New Requirement</SheetTitle>
              </SheetHeader>
              <RequirementForm setSheetOpen={setSheetOpen} />
            </SheetContent>
          </Sheet>
        </div>
      </div>
      
      {initialRequirements.length > 0 ? (
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {initialRequirements.map(req => (
            <RequirementCard key={req.id} requirement={req} />
          ))}
        </div>
      ) : (
        <div className="flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 bg-muted/20 p-12 text-center">
          <h3 className="text-lg font-semibold text-muted-foreground">No Requirements Found</h3>
          <p className="mt-2 text-sm text-muted-foreground">
            There are no requirements with the status "{currentStatus.replace('-', ' ')}".
          </p>
        </div>
      )}
    </div>
  );
}

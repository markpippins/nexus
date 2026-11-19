import { notFound } from 'next/navigation';
import { getRequirementById, deleteRequirement } from '@/lib/actions';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { StatusBadge } from '@/components/requirements/StatusBadge';
import { SubItemsList } from '@/components/requirements/SubItemsList';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { Pencil } from 'lucide-react';
import { RequirementForm } from '@/components/requirements/RequirementForm';
import { DeleteConfirmationDialog } from '@/components/shared/DeleteConfirmationDialog';

export default async function RequirementDetailPage({ params }: { params: { id: string } }) {
  const requirement = await getRequirementById(params.id);

  if (!requirement) {
    notFound();
  }

  const deleteAction = deleteRequirement.bind(null, requirement.id);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
            <div className="space-y-2">
              <StatusBadge status={requirement.status} />
              <CardTitle className="text-3xl font-bold font-headline">{requirement.name}</CardTitle>
              <CardDescription className="max-w-2xl">{requirement.description}</CardDescription>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
               <Sheet>
                <SheetTrigger asChild>
                  <Button variant="outline">
                    <Pencil className="mr-2 h-4 w-4" />
                    Edit
                  </Button>
                </SheetTrigger>
                <SheetContent className="sm:max-w-2xl w-full">
                  <SheetHeader>
                    <SheetTitle>Edit Requirement</SheetTitle>
                  </SheetHeader>
                  <RequirementForm requirement={requirement} />
                </SheetContent>
              </Sheet>
              <DeleteConfirmationDialog 
                action={deleteAction}
                itemType="requirement"
                redirectPath="/"
              />
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <h3 className="font-semibold mb-2 text-sm text-muted-foreground">Technologies</h3>
            <div className="flex flex-wrap gap-2">
              {requirement.technologies.map(tech => (
                <Badge key={tech} variant="secondary">
                  {tech}
                </Badge>
              ))}
            </div>
          </div>
          <div className="flex items-center gap-6 text-sm text-muted-foreground">
              <span>Created: {new Date(requirement.createdAt).toLocaleString()}</span>
              <span>Last Updated: {new Date(requirement.updatedAt).toLocaleString()}</span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sub-Items</CardTitle>
          <CardDescription>
            Manage the individual tasks and components for this requirement.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SubItemsList requirement={requirement} />
        </CardContent>
      </Card>
    </div>
  );
}

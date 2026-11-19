import { getRequirements } from '@/lib/actions';
import { RequirementDashboard } from '@/components/requirements/RequirementDashboard';
import type { Status } from '@/lib/types';

export default async function HomePage({
  searchParams,
}: {
  searchParams?: { [key: string]: string | string[] | undefined };
}) {
  const status = searchParams?.status as Status | undefined;
  const initialRequirements = await getRequirements(status);

  return <RequirementDashboard initialRequirements={initialRequirements} />;
}

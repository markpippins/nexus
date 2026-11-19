import { ForumCard } from '@/components/forums/forum-card';
import { getForums } from '@/lib/data';

export default async function ForumsPage() {
  const forums = await getForums();

  return (
    <div className="container mx-auto max-w-5xl px-4 py-8">
      <div className="mb-8 text-center">
        <h1 className="text-4xl font-bold font-headline tracking-tight">
          Forums
        </h1>
        <p className="mt-2 text-lg text-muted-foreground">
          Join the discussion in one of our communities.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
        {forums.map(forum => (
          <ForumCard key={forum.id} forum={forum} />
        ))}
      </div>
    </div>
  );
}


import { CreatePost } from '@/components/posts/create-post';
import { PostList } from '@/components/posts/post-list';
import { Suspense } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { HeroBanner } from '@/components/hero-banner';
import { FeedInfoCard } from '@/components/feed-info-card';

export default function FeedPage() {
  return (
    <div>
      <HeroBanner
        title="Your Feed"
        subtitle="See what's new in your network."
        imageId="homepage_banner"
      />
      <div className="container mx-auto max-w-6xl px-4 py-8">
        <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
          <aside className="md:col-span-1">
            <div className="sticky top-20">
              <FeedInfoCard />
            </div>
          </aside>
          <div className="space-y-8 md:col-span-2">
            <CreatePost />
            <Suspense fallback={<PostListSkeleton />}>
              <PostList />
            </Suspense>
          </div>
        </div>
      </div>
    </div>
  );
}

function PostListSkeleton() {
  return (
    <div className="space-y-6">
      {[...Array(3)].map((_, i) => (
        <div key={i} className="rounded-lg border bg-card p-6">
          <div className="flex items-start gap-4">
            <Skeleton className="h-10 w-10 rounded-full" />
            <div className="flex-1 space-y-2">
              <div className="flex items-center gap-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-4 w-16" />
              </div>
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

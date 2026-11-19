import { getPosts } from '@/lib/data';
import { PostCard } from './post-card';

export async function PostList() {
  const posts = await getPosts();
  return (
    <div className="space-y-6">
      {posts.map(post => (
        <PostCard key={post.id} post={post} />
      ))}
    </div>
  );
}

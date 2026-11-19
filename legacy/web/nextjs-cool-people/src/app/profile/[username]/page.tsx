import { PostList } from '@/components/posts/post-list';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { getPostsByUserId, getUserByUsername } from '@/lib/data';
import { PlaceHolderImages } from '@/lib/placeholder-images';
import { Mail, UserPlus } from 'lucide-react';
import { notFound } from 'next/navigation';
import { PostCard } from '@/components/posts/post-card';

export default async function ProfilePage({
  params,
}: {
  params: { username: string };
}) {
  const user = await getUserByUsername(params.username);
  if (!user) {
    notFound();
  }

  const userPosts = await getPostsByUserId(user.id);

  const avatarUrl =
    PlaceHolderImages.find(img => img.id === user.avatar)?.imageUrl || '';

  return (
    <div className="container mx-auto max-w-4xl px-4 py-8">
      <Card className="overflow-hidden">
        <div className="h-32 bg-muted md:h-48" />
        <CardContent className="p-4">
          <div className="relative -mt-16 flex flex-col items-center gap-4 text-center md:-mt-20 md:flex-row md:items-end md:text-left">
            <Avatar className="h-24 w-24 border-4 border-card md:h-32 md:w-32">
              <AvatarImage src={avatarUrl} alt={user.name} data-ai-hint="person portrait"/>
              <AvatarFallback className="text-4xl">
                {user.name.charAt(0)}
              </AvatarFallback>
            </Avatar>
            <div className="flex-1">
              <h1 className="text-2xl font-bold font-headline tracking-tight">
                {user.name}
              </h1>
              <p className="text-muted-foreground">@{user.username}</p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline">
                <Mail className="mr-2 h-4 w-4" /> Message
              </Button>
              <Button>
                <UserPlus className="mr-2 h-4 w-4" /> Follow
              </Button>
            </div>
          </div>
          <p className="mt-4 text-center md:text-left">{user.bio}</p>
        </CardContent>
      </Card>

      <Tabs defaultValue="posts" className="mt-8">
        <TabsList>
          <TabsTrigger value="posts">Posts</TabsTrigger>
          <TabsTrigger value="replies">Replies</TabsTrigger>
          <TabsTrigger value="likes">Likes</TabsTrigger>
        </TabsList>
        <TabsContent value="posts" className="mt-4">
          <div className="space-y-6">
            {userPosts.length > 0 ? (
              userPosts.map(post => <PostCard key={post.id} post={post} />)
            ) : (
              <Card className="flex h-48 items-center justify-center">
                <p className="text-muted-foreground">
                  @{user.username} hasn&apos;t posted yet.
                </p>
              </Card>
            )}
          </div>
        </TabsContent>
        <TabsContent value="replies">
          <Card className="flex h-48 items-center justify-center">
            <p className="text-muted-foreground">No replies to show.</p>
          </Card>
        </TabsContent>
        <TabsContent value="likes">
          <Card className="flex h-48 items-center justify-center">
            <p className="text-muted-foreground">No liked posts to show.</p>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

import { Button } from '@/components/ui/button';
import {
  getForumBySlug,
  getThreadsByForumSlug,
} from '@/lib/data';
import { notFound } from 'next/navigation';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import Link from 'next/link';
import { format, formatDistanceToNow } from 'date-fns';
import { PlaceHolderImages } from '@/lib/placeholder-images';

export default async function ForumSlugPage({
  params,
}: {
  params: { slug: string };
}) {
  const forum = await getForumBySlug(params.slug);
  if (!forum) {
    notFound();
  }
  const threads = await getThreadsByForumSlug(params.slug);

  return (
    <div className="container mx-auto max-w-5xl px-4 py-8">
      <div className="mb-8">
        <h1 className="text-4xl font-bold font-headline tracking-tight">
          {forum.name}
        </h1>
        <p className="mt-2 text-lg text-muted-foreground">
          {forum.description}
        </p>
      </div>

      <div className="mb-6 flex justify-end">
        <Button>Start New Discussion</Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[60%]">Topic</TableHead>
              <TableHead className="text-center">Replies</TableHead>
              <TableHead className="text-center">Views</TableHead>
              <TableHead className="text-right">Last Activity</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {threads.map(thread => {
              const authorAvatar = PlaceHolderImages.find(
                img => img.id === thread.author.avatar
              )?.imageUrl;
              const lastReplyUserAvatar = thread.lastReply ? PlaceHolderImages.find(
                img => img.id === thread.lastReply!.user.avatar
              )?.imageUrl : '';

              return (
                <TableRow key={thread.id}>
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <Link href={`/profile/${thread.author.username}`}>
                        <Avatar>
                          <AvatarImage src={authorAvatar} alt={thread.author.name} data-ai-hint="person portrait" />
                          <AvatarFallback>
                            {thread.author.name.charAt(0)}
                          </AvatarFallback>
                        </Avatar>
                      </Link>
                      <div>
                        <Link
                          href={`/forums/${forum.slug}/${thread.id}`}
                          className="font-medium hover:underline"
                        >
                          {thread.title}
                        </Link>
                        <div className="text-sm text-muted-foreground">
                          by{' '}
                          <Link
                            href={`/profile/${thread.author.username}`}
                            className="hover:underline"
                          >
                            {thread.author.name}
                          </Link>{' '}
                          on{' '}
                          {format(new Date(thread.createdAt), 'MMM d, yyyy')}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-center">
                    {thread.replyCount}
                  </TableCell>
                  <TableCell className="text-center">
                    {thread.viewCount}
                  </TableCell>
                  <TableCell className="text-right">
                    {thread.lastReply && (
                      <div className="flex items-center justify-end gap-2">
                        <div>
                          <div className='text-sm'>
                            by{' '}
                            <Link href={`/profile/${thread.lastReply.user.username}`} className="font-medium hover:underline">{thread.lastReply.user.name}</Link>
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {formatDistanceToNow(new Date(thread.lastReply.createdAt), { addSuffix: true })}
                          </div>
                        </div>
                        <Link href={`/profile/${thread.lastReply.user.username}`}>
                           <Avatar className="h-8 w-8">
                            <AvatarImage src={lastReplyUserAvatar} alt={thread.lastReply.user.name} data-ai-hint="person portrait"/>
                            <AvatarFallback>{thread.lastReply.user.name.charAt(0)}</AvatarFallback>
                          </Avatar>
                        </Link>
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

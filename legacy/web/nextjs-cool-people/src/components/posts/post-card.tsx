'use client';

import type { Post } from '@/lib/types';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from '@/components/ui/card';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import Link from 'next/link';
import { formatDistanceToNow } from 'date-fns';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Heart, MessageSquare, Share2 } from 'lucide-react';
import { PlaceHolderImages } from '@/lib/placeholder-images';

export function PostCard({ post }: { post: Post }) {
  const avatarUrl =
    PlaceHolderImages.find(img => img.id === post.user.avatar)?.imageUrl ||
    'https://picsum.photos/seed/placeholder/100/100';

  return (
    <Card>
      <CardHeader className="p-4">
        <div className="flex items-start gap-4">
          <Link href={`/profile/${post.user.username}`}>
            <Avatar>
              <AvatarImage src={avatarUrl} alt={post.user.name} data-ai-hint="person portrait"/>
              <AvatarFallback>
                {post.user.name.charAt(0)}
              </AvatarFallback>
            </Avatar>
          </Link>
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <Link
                href={`/profile/${post.user.username}`}
                className="font-bold font-headline hover:underline"
              >
                {post.user.name}
              </Link>
              <Link
                href={`/profile/${post.user.username}`}
                className="text-sm text-muted-foreground"
              >
                @{post.user.username}
              </Link>
              <span className="text-sm text-muted-foreground">·</span>
              <span className="text-sm text-muted-foreground hover:underline">
                <time dateTime={post.createdAt}>
                  {formatDistanceToNow(new Date(post.createdAt), {
                    addSuffix: true,
                  })}
                </time>
              </span>
            </div>
            <p className="text-sm text-muted-foreground">{post.user.bio}</p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <p className="whitespace-pre-wrap">{post.content}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          {post.tags.map(tag => (
            <Badge key={tag} variant="outline">
              #{tag}
            </Badge>
          ))}
        </div>
      </CardContent>
      <CardFooter className="p-4 pt-0">
        <div className="flex w-full items-center justify-between text-muted-foreground">
          <Button variant="ghost" size="sm">
            <MessageSquare className="mr-2 h-4 w-4" />
            {post.comments}
          </Button>
          <Button variant="ghost" size="sm">
            <Heart className="mr-2 h-4 w-4" />
            {post.likes}
          </Button>
          <Button variant="ghost" size="sm">
            <Share2 className="mr-2 h-4 w-4" />
            Share
          </Button>
        </div>
      </CardFooter>
    </Card>
  );
}

'use client';

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { PlaceHolderImages } from '@/lib/placeholder-images';
import { useAuth } from './auth-provider';
import Link from 'next/link';

export function FeedInfoCard() {
  const { user } = useAuth();

  if (!user) {
    return null;
  }

  const avatar = PlaceHolderImages.find(p => p.id === user.avatar);

  return (
    <Card>
      <CardContent className="p-6 text-center">
        <Link href={`/profile/${user.username}`}>
          <Avatar className="mx-auto h-24 w-24">
            {avatar && <AvatarImage src={avatar.imageUrl} alt={user.name} data-ai-hint={avatar.imageHint} />}
            <AvatarFallback>{user.name.charAt(0)}</AvatarFallback>
          </Avatar>
        </Link>
        <Link href={`/profile/${user.username}`}>
          <h2 className="mt-4 text-xl font-bold font-headline hover:underline">
            {user.name}
          </h2>
        </Link>
        <p className="text-sm text-muted-foreground">@{user.username}</p>
        <p className="mt-2 text-sm">{user.bio}</p>

        <div className="mt-6 flex justify-around text-center">
            <div>
                <p className="font-bold text-lg">23</p>
                <p className="text-xs text-muted-foreground">Posts</p>
            </div>
            <div>
                <p className="font-bold text-lg">1.2k</p>
                <p className="text-xs text-muted-foreground">Followers</p>
            </div>
            <div>
                <p className="font-bold text-lg">345</p>
                <p className="text-xs text-muted-foreground">Following</p>
            </div>
        </div>

        <Button asChild className="mt-6 w-full">
            <Link href={`/profile/${user.username}`}>View Profile</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

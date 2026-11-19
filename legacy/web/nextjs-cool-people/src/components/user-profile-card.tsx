'use client';

import { useAuth } from '@/components/auth-provider';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { LogIn, UserPlus, Settings } from 'lucide-react';
import Link from 'next/link';

export function UserProfileCard() {
  const { user, isLoggedIn } = useAuth();

  if (isLoggedIn && user) {
    // User is logged in
    return (
      <Card>
        <CardContent className="p-6 text-center">
          <Avatar className="mx-auto h-24 w-24">
            <AvatarImage src={`https://picsum.photos/seed/${user.id}/200/200`} alt={user.name} data-ai-hint="person abstract"/>
            <AvatarFallback>{user.name.charAt(0)}</AvatarFallback>
          </Avatar>
          <h2 className="mt-4 text-xl font-bold font-headline">
            {user.name}
          </h2>
          <p className="mt-1 text-muted-foreground text-sm">
            @{user.username}
          </p>
          <div className="mt-4 flex flex-col gap-2">
            <Link href={`/profile/${user.username}`} className="w-full">
              <Button variant="outline" className="w-full">
                View Profile
              </Button>
            </Link>
            <Link href="/profile/settings" className="w-full">
              <Button className="w-full">
                <Settings className="mr-2 h-4 w-4" />
                Settings
              </Button>
            </Link>
          </div>
        </CardContent>
      </Card>
    );
  } else {
    // User is not logged in
    return (
      <Card>
        <CardContent className="p-6 text-center">
          <Avatar className="mx-auto h-24 w-24">
            <AvatarImage src="https://picsum.photos/seed/currentuser/200/200" alt="Guest" data-ai-hint="person abstract"/>
            <AvatarFallback>G</AvatarFallback>
          </Avatar>
          <h2 className="mt-4 text-xl font-bold font-headline">
            Join the Conversation
          </h2>
          <p className="mt-1 text-muted-foreground">
            Sign up to share your thoughts, connect with others, and customize
            your feed.
          </p>
          <div className="mt-6 flex flex-col gap-2">
            <Link href="/signup" className="w-full">
              <Button className="w-full">
                <UserPlus className="mr-2" />
                Sign Up
              </Button>
            </Link>
            <Link href="/login" className="w-full">
              <Button variant="outline" className="w-full">
                <LogIn className="mr-2" />
                Login
              </Button>
            </Link>
          </div>
        </CardContent>
      </Card>
    );
  }
}

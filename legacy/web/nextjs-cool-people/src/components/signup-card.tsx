'use client';

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { LogIn, UserPlus } from 'lucide-react';
import Link from 'next/link';

export function SignupCard() {
  // In a real app, you'd fetch the current user's data here.
  // For now, we'll show a generic card for logged-out users.

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

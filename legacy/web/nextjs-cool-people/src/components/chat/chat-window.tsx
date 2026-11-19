'use client';

import { useState } from 'react';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { ChevronDown, Minus, Send, X } from 'lucide-react';
import { useChat } from './chat-provider';
import { cn } from '@/lib/utils';
import { useAuth } from '../auth-provider';

export function ChatWindow() {
  const { user } = useAuth();
  const { isChatOpen, closeChat } = useChat();
  const [isMinimized, setIsMinimized] = useState(false);

  if (!isChatOpen || !user) {
    return null;
  }

  const toggleMinimized = () => setIsMinimized(prev => !prev);

  return (
    <div className="fixed bottom-4 right-4 z-50">
      <Card
        className={cn(
          'w-80 overflow-hidden shadow-lg transition-all',
          isMinimized ? 'h-12' : 'h-96'
        )}
      >
        <CardHeader className="flex flex-row items-center justify-between bg-primary p-2 text-primary-foreground">
          <h3 className="font-bold">Chat</h3>
          <div className="flex items-center gap-1">
            <Button size="icon" variant="ghost" onClick={toggleMinimized} className="h-6 w-6 hover:bg-primary/80">
              {isMinimized ? <ChevronDown className="rotate-180"/> : <Minus />}
            </Button>
            <Button size="icon" variant="ghost" onClick={closeChat} className="h-6 w-6 hover:bg-primary/80">
              <X />
            </Button>
          </div>
        </CardHeader>
        {!isMinimized && (
          <>
            <CardContent className="flex h-[calc(100%-120px)] flex-col gap-4 overflow-y-auto p-4">
              {/* Placeholder messages */}
              <div className="flex items-start gap-2">
                <Avatar className="h-8 w-8">
                  <AvatarImage src="https://picsum.photos/seed/friend1/100/100" />
                  <AvatarFallback>F</AvatarFallback>
                </Avatar>
                <div className="rounded-lg bg-muted p-2 text-sm">
                  Hey, how's it going?
                </div>
              </div>
              <div className="flex flex-row-reverse items-start gap-2">
                <Avatar className="h-8 w-8">
                  <AvatarImage src="https://picsum.photos/seed/currentuser/100/100" />
                  <AvatarFallback>U</AvatarFallback>
                </Avatar>
                <div className="rounded-lg bg-primary p-2 text-sm text-primary-foreground">
                  Pretty good! Just working on this new app.
                </div>
              </div>
            </CardContent>
            <CardFooter className="p-2">
              <div className="flex w-full items-center gap-2">
                <Textarea
                  placeholder="Type a message..."
                  className="h-10 flex-1 resize-none"
                />
                <Button size="icon">
                  <Send />
                </Button>
              </div>
            </CardFooter>
          </>
        )}
      </Card>
    </div>
  );
}

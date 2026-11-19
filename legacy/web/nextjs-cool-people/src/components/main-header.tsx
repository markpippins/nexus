
'use client';

import Link from 'next/link';
import { Button } from './ui/button';
import { ThemeToggle } from './theme-toggle';
import {
  LogIn,
  LogOut,
  MessageSquare,
  Rss,
  User,
  UserPlus,
  Users,
  Zap,
  Menu,
  Settings,
} from 'lucide-react';
import { useAuth } from './auth-provider';
import { useRouter } from 'next/navigation';
import { useToast } from '@/hooks/use-toast';
import { useChat } from './chat/chat-provider';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from './ui/dropdown-menu';

function Logo() {
  return (
    <Link
      href="/"
      className="flex items-center gap-2 transition-opacity hover:opacity-80"
    >
      <div className="rounded-lg bg-primary/10 p-2">
        <Zap className="h-5 w-5 text-primary" />
      </div>
      <span className="text-xl font-bold font-headline tracking-tight">
        CoolPeople
      </span>
    </Link>
  );
}

export function MainHeader() {
  const { user, logout } = useAuth();
  const { toggleChat } = useChat();
  const router = useRouter();
  const { toast } = useToast();

  const handleLogout = () => {
    logout();
    toast({
      title: 'Logged Out',
      description: 'You have been successfully logged out.',
    });
    router.push('/');
  };

  return (
    <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-16 items-center">
        <Logo />
        <div className="ml-auto flex items-center space-x-2">
          {user ? (
            <>
              <Button variant="ghost" onClick={toggleChat} size="icon" className="md:hidden">
                 <MessageSquare size={16} />
              </Button>
               <Button variant="ghost" onClick={toggleChat} className="hidden md:flex">
                <MessageSquare size={16} className="mr-2" />
                Chat
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon">
                    <Menu />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuLabel>My Account</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem asChild>
                    <Link href="/feed">
                      <Rss className="mr-2 h-4 w-4" />
                      <span>Feed</span>
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                    <Link href={`/profile/${user.username}`}>
                      <User className="mr-2 h-4 w-4" />
                      <span>Profile</span>
                    </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                     <Link href="/forums">
                       <Users className="mr-2 h-4 w-4" />
                       <span>Forums</span>
                     </Link>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                     <Link href="/users">
                       <Users className="mr-2 h-4 w-4" />
                       <span>Users</span>
                     </Link>
                  </DropdownMenuItem>
                   <DropdownMenuItem asChild>
                     <Link href="/profile/settings">
                       <Settings className="mr-2 h-4 w-4" />
                       <span>Settings</span>
                     </Link>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <div className="px-2 py-1.5">
                    <ThemeToggle />
                  </div>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleLogout}>
                    <LogOut className="mr-2 h-4 w-4" />
                    <span>Logout</span>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          ) : (
            <>
              <Link href="/login">
                <Button variant="ghost">
                  <LogIn size={16} className="mr-2" />
                  Login
                </Button>
              </Link>
              <Link href="/signup">
                <Button>
                  <UserPlus size={16} className="mr-2" />
                  Sign Up
                </Button>
              </Link>
               <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon">
                    <Menu />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                   <DropdownMenuLabel>Menu</DropdownMenuLabel>
                   <DropdownMenuSeparator />
                   <div className="px-2 py-1.5">
                    <ThemeToggle />
                  </div>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          )}
        </div>
      </div>
    </header>
  );
}

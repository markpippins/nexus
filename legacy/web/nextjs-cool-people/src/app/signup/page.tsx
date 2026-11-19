'use client';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { Loader2 } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
// import { useAuth } from '@/components/auth-provider'; // No longer using client-side auth for signup
import { signupAction } from '@/lib/actions'; // Use server action

export default function SignupPage() {
  const { toast } = useToast();
  const router = useRouter();
  // const { login } = useAuth(); 
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);

    try {
      const formData = new FormData();
      formData.append('name', name);
      formData.append('email', email);
      formData.append('password', password);

      const result = await signupAction(null, formData);

      if (result?.type === 'error') {
        throw new Error(result.message);
      }

      // signupAction redirects on success, but if it returns (e.g. due to redirect behavior in client component),
      // we can handle it. Actually redirect throws, so we might not get here if successful.
      // But if we do (e.g. if we change implementation), we can show toast.
      // However, redirect in Server Action usually handles the navigation.
      // If we want to show toast, we might need to do it differently or assume success if no error.

      // Note: redirect() in Server Action throws an error that Next.js catches. 
      // So we won't reach here unless we catch that error.
      // But we are calling it from a client component wrapper? 
      // No, we are calling the async function.

      // Let's assume if it returns, it failed or we need to manual redirect if not using redirect() in action.
      // My signupAction uses redirect().

    } catch (error) {
      // Check if it's a redirect error (NEXT_REDIRECT)
      if ((error as any).message === 'NEXT_REDIRECT') {
        throw error;
      }

      console.error('Signup error:', error);
      toast({
        title: 'Signup Failed',
        description: error instanceof Error ? error.message : 'Signup failed',
        variant: 'destructive',
      });
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex min-h-[calc(100vh-4rem)] items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <form onSubmit={handleSubmit}>
          <CardHeader>
            <CardTitle className="font-headline text-2xl">Sign Up</CardTitle>
            <CardDescription>
              Create an account to join the conversation.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                name="name"
                placeholder="John Doe"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                name="email"
                type="email"
                placeholder="m@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
          </CardContent>
          <CardFooter className="flex flex-col gap-4">
            <Button className="w-full" disabled={isLoading}>
              {isLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Create Account
            </Button>
            <div className="text-center text-sm">
              Already have an account?{' '}
              <Link href="/login" className="underline">
                Login
              </Link>
            </div>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}

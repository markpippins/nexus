import type { Metadata } from 'next';
import './globals.css';
import { cn } from '@/lib/utils';
import { ThemeProvider } from '@/components/theme-provider';
import { MainHeader } from '@/components/main-header';
import { Toaster } from '@/components/ui/toaster';
import { AuthProvider } from '@/components/auth-provider';
import { ChatProvider } from '@/components/chat/chat-provider';
import { ChatWindow } from '@/components/chat/chat-window';

export const metadata: Metadata = {
  title: 'CoolPeople',
  description: 'A modern social media platform built for connection.',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin="anonymous"
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;600;700&family=PT+Sans:wght@400;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body
        className={cn('min-h-screen bg-background font-body antialiased')}
        suppressHydrationWarning
      >
        <ThemeProvider>
          <AuthProvider>
            <ChatProvider>
              <div className="relative flex min-h-dvh flex-col">
                <MainHeader />
                <main className="flex-1">{children}</main>
              </div>
              <ChatWindow />
              <Toaster />
            </ChatProvider>
          </AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}

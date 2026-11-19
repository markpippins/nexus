import { ApiTester } from "@/components/api-tester";

export default function HelloApiPage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        <h1 className="text-4xl font-bold tracking-tight">Hello API Test</h1>
        <p className="text-muted-foreground text-center">
          A simple interface to send POST requests and view responses.
        </p>
      </div>
      <ApiTester />
    </main>
  );
}

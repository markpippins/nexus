"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Send, FileJson, Loader2, Server, Terminal } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

const formSchema = z.object({
  id: z.coerce.number().int().positive("ID must be a positive integer"),
  name: z.string().min(2, "Name must be at least 2 characters"),
  message: z.string().min(5, "Message must be at least 5 characters"),
});

type ResponseState = {
  data: string | null;
  error: boolean;
};

export function ApiTester() {
  const [response, setResponse] = useState<ResponseState>({
    data: null,
    error: false,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const { toast } = useToast();

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      id: 1,
      name: "John Doe",
      message: "This is a test message.",
    },
  });

  const handleRequest = async (
    requestFn: () => Promise<Response>
  ): Promise<void> => {
    setLoading(true);
    setResponse({ data: null, error: false });
    try {
      const res = await requestFn();
      const data = await res.text();
      if (!res.ok) throw new Error(data || `Server responded with ${res.status}`);
      setResponse({ data, error: false });
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "An unknown error occurred";
      setResponse({ data: `Failed to fetch: ${errorMessage}`, error: true });
      toast({
        variant: "destructive",
        title: "API Error",
        description: errorMessage,
      });
    }
    setLoading(false);
  };

  const handleSimplePost = () => {
    handleRequest(() => fetch("/api/submit", { method: "POST" }));
  };

  const onJsonSubmit = (values: z.infer<typeof formSchema>) => {
    handleRequest(() =>
      fetch("/api/submitRequest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      })
    );
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <div className="flex flex-col gap-8">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Send className="h-5 w-5" />
              Simple POST Request
            </CardTitle>
            <CardDescription>
              Sends a simple POST request to the <code>/api/submit</code> endpoint.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              onClick={handleSimplePost}
              disabled={loading}
              className="w-full"
            >
              {loading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                "Send POST to /api/submit"
              )}
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileJson className="h-5 w-5" />
              JSON POST Request
            </CardTitle>
            <CardDescription>
              Sends a POST request with a JSON payload to the{" "}
              <code>/api/submitRequest</code> endpoint.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onJsonSubmit)}
                className="space-y-6"
              >
                <FormField
                  control={form.control}
                  name="id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>ID</FormLabel>
                      <FormControl>
                        <Input type="number" placeholder="e.g., 123" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name</FormLabel>
                      <FormControl>
                        <Input placeholder="e.g., John Doe" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="message"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Message</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder="Type your message here..."
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button type="submit" disabled={loading} className="w-full">
                  {loading ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    "Send JSON to /api/submitRequest"
                  )}
                </Button>
              </form>
            </Form>
          </CardContent>
        </Card>
      </div>

      <div className="lg:sticky lg:top-8">
        <Card
          className={cn(
            "transition-all",
            response.data && !response.error && "border-green-400",
            response.data && response.error && "border-destructive"
          )}
        >
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-5 w-5" />
              Server Response
            </CardTitle>
            <CardDescription>
              The response from the server will be displayed below.
            </CardDescription>
          </CardHeader>
          <CardContent className="min-h-[200px] flex items-center justify-center rounded-lg bg-muted/20">
            {loading ? (
              <div className="flex flex-col items-center gap-2 text-muted-foreground">
                <Loader2 className="h-8 w-8 animate-spin" />
                <span>Awaiting response...</span>
              </div>
            ) : response.data ? (
              <pre
                className={cn(
                  "w-full bg-muted/50 p-4 rounded-md text-sm overflow-x-auto",
                  response.error
                    ? "text-destructive-foreground bg-destructive"
                    : "text-foreground"
                )}
              >
                <code>{response.data}</code>
              </pre>
            ) : (
              <div className="text-center text-muted-foreground">
                <Terminal className="mx-auto h-12 w-12 mb-2" />
                <p>No response yet. Send a request to see the result.</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

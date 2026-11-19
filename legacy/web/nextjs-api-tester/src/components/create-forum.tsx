"use client";

import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, Server, Terminal, MessageSquarePlus } from "lucide-react";
import { v4 as uuidv4 } from "uuid";

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
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

const formSchema = z.object({
  name: z.string().min(3, "Forum name must be at least 3 characters."),
});

type ResponseState = {
  data: string | null;
  error: boolean;
};

export function CreateForum() {
  const [response, setResponse] = useState<ResponseState>({
    data: null,
    error: false,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const { toast } = useToast();

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: "",
    },
  });

  const handleRequest = async (
    requestFn: () => Promise<Response>
  ): Promise<void> => {
    setLoading(true);
    setResponse({ data: null, error: false });
    try {
      const res = await requestFn();
      const data = await res.json();
      const formattedData = JSON.stringify(data, null, 2);

      if (!res.ok) {
        throw new Error(
          `Server responded with ${res.status}: ${formattedData}`
        );
      }
      setResponse({ data: formattedData, error: false });
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

  const onSubmit = (values: z.infer<typeof formSchema>) => {
    const requestBody = {
      service: "forumService",
      operation: "create",
      requestId: uuidv4(),
      params: { name: values.name },
    };
    
    handleRequest(() =>
      fetch("/api/broker/submitRequest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      })
    );
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquarePlus className="h-5 w-5" />
            New Forum Details
          </CardTitle>
          <CardDescription>
            Enter the details for the new forum.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Form {...form}>
            <form
              onSubmit={form.handleSubmit(onSubmit)}
              className="space-y-6"
            >
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Forum Name</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g., General Discussion" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  "Create Forum"
                )}
              </Button>
            </form>
          </Form>
        </CardContent>
      </Card>

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
              Broker Response
            </CardTitle>
            <CardDescription>
              The response from the broker service will be displayed below.
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
                <p>No response yet. Submit the form to create a forum.</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

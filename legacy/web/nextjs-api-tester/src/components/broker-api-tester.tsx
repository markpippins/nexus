"use client";

import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Send, Loader2, Server, Terminal, Workflow } from "lucide-react";
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
import { Textarea } from "@/components/ui/textarea";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";

const formSchema = z.object({
  service: z.string().min(2, "Service must be at least 2 characters"),
  operation: z.string().min(2, "Operation must be at least 2 characters"),
  requestId: z.string().uuid("Must be a valid UUID"),
  params: z.string().refine(
    (val) => {
      try {
        JSON.parse(val);
        return true;
      } catch (e) {
        return false;
      }
    },
    { message: "Parameters must be valid JSON" }
  ),
});

type ResponseState = {
  data: string | null;
  error: boolean;
};

export function BrokerApiTester() {
  const [response, setResponse] = useState<ResponseState>({
    data: null,
    error: false,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const { toast } = useToast();

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      service: "test",
      operation: "echo",
      requestId: "",
      params: JSON.stringify({ key1: "value1", key2: 123 }, null, 2),
    },
  });

  useEffect(() => {
    form.setValue("requestId", uuidv4());
  }, [form]);

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

  const onJsonSubmit = (values: z.infer<typeof formSchema>) => {
    const parsedParams = JSON.parse(values.params);
    handleRequest(() =>
      fetch("/api/broker/submitRequest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...values, params: parsedParams }),
      })
    );
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Workflow className="h-5 w-5" />
            Broker Service Request
          </CardTitle>
          <CardDescription>
            Send a request to the <code>/api/broker/submitRequest</code>{" "}
            endpoint.
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
                name="service"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Service</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g., test" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="operation"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Operation</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g., echo" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="requestId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Request ID</FormLabel>
                    <div className="flex items-center gap-2">
                      <FormControl>
                        <Input placeholder="UUID" {...field} />
                      </FormControl>
                      <Button
                        type="button"
                        variant="outline"
                        onClick={() => form.setValue("requestId", uuidv4())}
                      >
                        New UUID
                      </Button>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="params"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Parameters (JSON)</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder='{ "key": "value" }'
                        className="font-code h-32"
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
                  "Send Broker Request"
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

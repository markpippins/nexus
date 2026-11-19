"use client";

import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, Server, Terminal, UserPlus } from "lucide-react";
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
import { useBrokerApi } from "@/hooks/use-broker-api";

const formSchema = z.object({
  email: z.string().email("Please enter a valid email address."),
  alias: z.string().min(3, "Username must be at least 3 characters."),
  identifier: z.string().min(1, "Identifier is required."),
});

type ResponseState = {
  data: string | null;
  error: boolean;
};

export function CreateUser() {
  const { callBroker } = useBrokerApi();
  const [response, setResponse] = useState<ResponseState>({
    data: null,
    error: false,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const { toast } = useToast();

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      email: "",
      alias: "",
      identifier: "",
    },
  });

  const onSubmit = async (values: z.infer<typeof formSchema>) => {
    setLoading(true);
    setResponse({ data: null, error: false });
    try {
      const result = await callBroker("userService", "createUser", values);

      if (result.error) {
        throw new Error(result.error);
      }

      const formattedData = JSON.stringify(result, null, 2);
      setResponse({ data: formattedData, error: false });
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "An unknown error occurred";
      setResponse({ data: `Failed to create user: ${errorMessage}`, error: true });
      toast({
        variant: "destructive",
        title: "API Error",
        description: errorMessage,
      });
    }
    setLoading(false);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UserPlus className="h-5 w-5" />
            New User Details
          </CardTitle>
          <CardDescription>
            Enter the details for the new user.
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
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input type="email" placeholder="user@example.com" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="alias"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Username</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g., john.doe" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="identifier"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Identifier</FormLabel>
                     <FormControl>
                      <Input placeholder="e.g., user-123" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  "Create User"
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
                <p>No response yet. Submit the form to create a user.</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

"use client";

import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, Server, Terminal, UploadCloud } from "lucide-react";
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
  service: z.string().min(2, "Service must be at least 2 characters."),
  operation: z.string().min(2, "Operation must be at least 2 characters."),
  params: z.string().optional(),
  requestId: z.string().uuid(),
  file: (typeof window === 'undefined' ? z.any() : z.instanceof(FileList)).refine((files) => files?.length === 1, "File is required."),
});

type ResponseState = {
  data: string | null;
  error: boolean;
};

export function FileUpload() {
  const [response, setResponse] = useState<ResponseState>({
    data: null,
    error: false,
  });
  const [loading, setLoading] = useState<boolean>(false);
  const { toast } = useToast();

  const form = useForm<z.infer<typeof formSchema>>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      service: "file-storage",
      operation: "upload",
      params: JSON.stringify({ path: "/uploads" }, null, 2),
      requestId: "",
    },
  });
  const fileRef = form.register("file");

  useEffect(() => {
    form.setValue("requestId", uuidv4());
  }, [form]);


  const handleRequest = async (formData: FormData): Promise<void> => {
    setLoading(true);
    setResponse({ data: null, error: false });
    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData,
      });
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
    const formData = new FormData();
    formData.append("service", values.service);
    formData.append("operation", values.operation);
    if (values.params) {
      formData.append("params", values.params);
    }
    formData.append("requestId", values.requestId);
    if (values.file && values.file.length > 0) {
      formData.append("file", values.file[0]);
    }

    handleRequest(formData);
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 items-start">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <UploadCloud className="h-5 w-5" />
            File Upload Request
          </CardTitle>
          <CardDescription>
            Submit a multipart/form-data request to the backend.
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
                name="service"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Service</FormLabel>
                    <FormControl>
                      <Input placeholder="e.g., file-storage" {...field} />
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
                      <Input placeholder="e.g., upload" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="params"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Parameters (JSON, optional)</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder='{ "key": "value" }'
                        className="font-code h-24"
                        {...field}
                      />
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
                        <Input placeholder="UUID" {...field} value={field.value || ''} />
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
                name="file"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>File</FormLabel>
                    <FormControl>
                      <Input type="file" {...fileRef} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <Button type="submit" disabled={loading} className="w-full">
                {loading ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  "Upload File"
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
                <p>No response yet. Submit the form to upload a file.</p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

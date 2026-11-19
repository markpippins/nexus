"use client";

import { useState, useEffect } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Loader2, Users, UserPlus, RefreshCw } from "lucide-react";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useToast } from "@/hooks/use-toast";

const userFormSchema = z.object({
  email: z.string().email("Please enter a valid email address."),
  alias: z.string().min(3, "Username must be at least 3 characters."),
  identifier: z.string().min(1, "Identifier is required."),
});

type User = {
  id: string;
  email: string;
  alias: string;
  profileId?: string;
  avatarUrl?: string;
  admin?: boolean;
};

type ServiceResponse = {
  ok: boolean;
  data?: User[];
  error?: string;
  errors?: { message: string }[];
};

export function UserManagement() {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [createLoading, setCreateLoading] = useState(false);
  const { toast } = useToast();

  const form = useForm<z.infer<typeof userFormSchema>>({
    resolver: zodResolver(userFormSchema),
    defaultValues: {
      email: "",
      alias: "",
      identifier: "",
    },
  });

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const requestBody = {
        service: "userService",
        operation: "findAll",
        requestId: uuidv4(),
        params: {},
      };

      const response = await fetch("/api/broker/submitRequest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });

      const data: ServiceResponse = await response.json();
      
      if (!response.ok || !data.ok) {
        const errorDetails = data.errors?.[0]?.message || data.error || "Failed to fetch users";
        throw new Error(errorDetails);
      }

      // If successful, update users list
      if (data.data) {
        setUsers(data.data);
      }
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "An unknown error occurred";
      toast({
        variant: "destructive",
        title: "Failed to Load Users",
        description: errorMessage,
      });
    }
    setLoading(false);
  };

  const createUser = async (values: z.infer<typeof userFormSchema>) => {
    setCreateLoading(true);
    try {
      const requestBody = {
        service: "userService",
        operation: "createUser",
        requestId: uuidv4(),
        params: values,
      };

      const response = await fetch("/api/broker/submitRequest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });

      const data: ServiceResponse = await response.json();
      
      if (!response.ok || !data.ok) {
        const errorDetails = data.errors?.[0]?.message || data.error || "User creation failed";
        throw new Error(errorDetails);
      }

      toast({
        title: "User Created Successfully",
        description: `User "${values.alias}" has been created.`,
      });
      
      setCreateDialogOpen(false);
      form.reset();
      
      // Refresh the user list
      fetchUsers();
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : "An unknown error occurred";
      toast({
        variant: "destructive",
        title: "User Creation Failed",
        description: errorMessage,
      });
    }
    setCreateLoading(false);
  };

  // Load users on component mount
  useEffect(() => {
    fetchUsers();
  }, []);

  return (
    <div className="h-screen flex flex-col bg-background text-foreground">
      <main className="flex-1 flex items-center justify-center p-4">
        <Card className="shadow-lg h-full max-h-[90vh] w-full max-w-6xl flex flex-col">
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Users className="h-5 w-5" />
                  User Management
                </CardTitle>
                <CardDescription>
                  Manage system users and their accounts.
                </CardDescription>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={fetchUsers} disabled={loading}>
                  <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                  Refresh
                </Button>
                <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
                  <DialogTrigger asChild>
                    <Button size="sm">
                      <UserPlus className="h-4 w-4 mr-2" />
                      Add User
                    </Button>
                  </DialogTrigger>
                  <DialogContent className="sm:max-w-[425px]">
                    <DialogHeader>
                      <DialogTitle className="flex items-center gap-2">
                        <UserPlus className="h-5 w-5" />
                        Create New User
                      </DialogTitle>
                      <DialogDescription>
                        Enter the details for the new user account.
                      </DialogDescription>
                    </DialogHeader>
                    <Form {...form}>
                      <form onSubmit={form.handleSubmit(createUser)} className="space-y-4">
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
                        <DialogFooter>
                          <Button type="submit" disabled={createLoading} className="w-full">
                            {createLoading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                            Create User
                          </Button>
                        </DialogFooter>
                      </form>
                    </Form>
                  </DialogContent>
                </Dialog>
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex-1 overflow-auto">
            {loading ? (
              <div className="flex items-center justify-center h-full">
                <div className="flex flex-col items-center gap-2 text-muted-foreground">
                  <Loader2 className="h-8 w-8 animate-spin" />
                  <span>Loading users...</span>
                </div>
              </div>
            ) : users.length > 0 ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Username</TableHead>
                    <TableHead>Email</TableHead>
                    <TableHead>User ID</TableHead>
                    <TableHead>Profile ID</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {users.map((user) => (
                    <TableRow key={user.id}>
                      <TableCell className="font-medium">{user.alias}</TableCell>
                      <TableCell>{user.email}</TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {user.id}
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {user.profileId || 'N/A'}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <div className="flex items-center justify-center h-full">
                <div className="text-center text-muted-foreground">
                  <Users className="mx-auto h-12 w-12 mb-4 opacity-50" />
                  <p>No users found. Click "Add User" to create one.</p>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
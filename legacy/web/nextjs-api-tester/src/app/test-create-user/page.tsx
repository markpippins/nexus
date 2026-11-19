import { CreateUser } from "@/components/create-user-test";

export default function CreateUserTestPage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        <h1 className="text-4xl font-bold tracking-tight">
          Create User
        </h1>
        <p className="text-muted-foreground text-center">
          Use the broker API to create a new user.
        </p>
      </div>
      <CreateUser />
    </main>
  );
}

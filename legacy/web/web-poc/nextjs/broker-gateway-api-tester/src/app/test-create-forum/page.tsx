import { CreateForum } from "@/components/create-forum";

export default function CreateForumPage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        <h1 className="text-4xl font-bold tracking-tight">
          Create Forum
        </h1>
        <p className="text-muted-foreground text-center">
          Use the broker API to create a new forum.
        </p>
      </div>
      <CreateForum />
    </main>
  );
}

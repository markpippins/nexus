import { FileNavigator } from "@/components/file-navigator";

export default function FileNavigatorPage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        {/* <h1 className="text-4xl font-bold tracking-tight">File Navigator</h1>
        <p className="text-muted-foreground text-center">
          Browse and manage files using the /fs API.
        </p> */}
      </div>
      <FileNavigator />
    </main>
  );
}

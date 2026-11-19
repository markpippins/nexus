import { FileUpload } from "@/components/file-upload";

export default function FileUploadPage() {
  return (
    <main className="container mx-auto p-4 sm:p-8">
      <div className="flex flex-col items-center justify-center space-y-2 mb-12">
        <h1 className="text-4xl font-bold tracking-tight">File Upload</h1>
        <p className="text-muted-foreground text-center">
          Upload a file to the backend service.
        </p>
      </div>
      <FileUpload />
    </main>
  );
}

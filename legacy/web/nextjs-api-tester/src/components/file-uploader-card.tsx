'use client';

import { useState, useCallback, useEffect } from 'react';
import { useForm, type SubmitHandler } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { useToast } from '@/hooks/use-toast';
import { UploadCloud, FileText, XCircle } from 'lucide-react';
import type { Scan } from '@/types';

const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
const ACCEPTED_FILE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'application/pdf'];

const formSchema = z.object({
  file: z
    .custom<FileList>((val) => val instanceof FileList && val.length > 0, 'Please select a file.')
    .refine((files) => files?.[0]?.size <= MAX_FILE_SIZE, `File size should be less than 5MB.`)
    .refine(
      (files) => ACCEPTED_FILE_TYPES.includes(files?.[0]?.type),
      'Invalid file type. Accepted types: JPG, PNG, GIF, PDF.'
    ),
});

type FormValues = z.infer<typeof formSchema>;

interface FileUploaderCardProps {
  onFileProcessed: (newScan: Scan | null) => void;
  isProcessing: boolean;
  setIsProcessing: (isProcessing: boolean) => void;
  processFileAction: (formData: FormData) => Promise<{ scan?: Scan; error?: string }>;
}

export default function FileUploaderCard({ onFileProcessed, isProcessing, setIsProcessing, processFileAction }: FileUploaderCardProps) {
  const { toast } = useToast();
  const [dragActive, setDragActive] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0); // For visual feedback, not actual progress tracking here

  const {
    register,
    handleSubmit,
    formState: { errors },
    reset,
    setValue,
    watch,
  } = useForm<FormValues>({
    resolver: zodResolver(formSchema),
  });

  const selectedFile = watch('file');

  useEffect(() => {
    if (selectedFile && selectedFile.length > 0) {
      setFileName(selectedFile[0].name);
    } else {
      setFileName(null);
    }
  }, [selectedFile]);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragActive(false);
      if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        setValue('file', e.dataTransfer.files, { shouldValidate: true });
      }
    },
    [setValue]
  );
  
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      setValue('file', e.target.files, { shouldValidate: true });
    }
  };

  const removeFile = () => {
    reset({ file: undefined });
    setFileName(null);
    setUploadProgress(0);
  };

  const onSubmit: SubmitHandler<FormValues> = async (data) => {
    if (!data.file || data.file.length === 0) return;

    setIsProcessing(true);
    setUploadProgress(0); // Reset progress

    // Simulate progress for UI
    let currentProgress = 0;
    const progressInterval = setInterval(() => {
      currentProgress += 10;
      if (currentProgress <= 90) { // Don't let simulated progress hit 100% until action is done
        setUploadProgress(currentProgress);
      }
    }, 200);

    const formData = new FormData();
    formData.append('file', data.file[0]);

    try {
      const result = await processFileAction(formData);
      clearInterval(progressInterval);
      setUploadProgress(100);

      if (result.error) {
        toast({
          title: 'Error Processing File',
          description: result.error,
          variant: 'destructive',
        });
        onFileProcessed(null); // Pass null to indicate error
      } else if (result.scan) {
        toast({
          title: 'File Processed',
          description: `${result.scan.fileName} has been successfully processed.`,
        });
        onFileProcessed(result.scan);
      }
    } catch (error) {
      clearInterval(progressInterval);
      setUploadProgress(0);
      toast({
        title: 'Upload Failed',
        description: 'An unexpected error occurred.',
        variant: 'destructive',
      });
      onFileProcessed(null);
    } finally {
      setIsProcessing(false);
      // Delay reset to show 100% progress briefly
      setTimeout(() => {
         reset(); // Reset form fields
         setFileName(null);
         setUploadProgress(0);
      }, 1000);
    }
  };

  return (
    <Card className="shadow-lg">
      <CardHeader>
        <CardTitle>Upload Document for OCR</CardTitle>
        <CardDescription>Drag and drop a file or click to select. Max 5MB. (JPG, PNG, GIF, PDF)</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-6">
          <div
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
            className={`flex flex-col items-center justify-center p-8 border-2 border-dashed rounded-lg cursor-pointer
            ${dragActive ? 'border-primary bg-accent/20' : 'border-input hover:border-primary/70'}
            ${errors.file ? 'border-destructive' : ''}
            transition-colors duration-200 ease-in-out`}
          >
            <Input
              {...register('file')}
              id="file-upload"
              type="file"
              className="hidden"
              accept={ACCEPTED_FILE_TYPES.join(',')}
              onChange={handleFileChange}
              disabled={isProcessing}
            />
            <Label htmlFor="file-upload" className="w-full h-full flex flex-col items-center justify-center cursor-pointer">
              <UploadCloud className={`w-12 h-12 mb-3 ${dragActive ? 'text-primary' : 'text-muted-foreground'}`} />
              <p className="text-sm text-muted-foreground">
                <span className="font-semibold text-primary">Click to upload</span> or drag and drop
              </p>
              <p className="text-xs text-muted-foreground">Supported: JPG, PNG, GIF, PDF (Max 5MB)</p>
            </Label>
          </div>

          {errors.file && <p className="text-sm text-destructive">{errors.file.message}</p>}
          
          {fileName && !isProcessing && (
            <div className="mt-4 p-3 border rounded-md flex items-center justify-between bg-muted/50">
              <div className="flex items-center gap-2">
                <FileText className="w-5 h-5 text-primary" />
                <span className="text-sm font-medium">{fileName}</span>
              </div>
              <Button type="button" variant="ghost" size="icon" onClick={removeFile} className="text-destructive hover:bg-destructive/10">
                <XCircle className="w-5 h-5" />
                <span className="sr-only">Remove file</span>
              </Button>
            </div>
          )}

          {isProcessing && (
            <div className="space-y-2 pt-2">
              <Progress value={uploadProgress} className="w-full" />
              <p className="text-sm text-muted-foreground text-center">
                {uploadProgress < 100 ? `Processing ${fileName}... ${uploadProgress}%` : 'Finalizing...'}
              </p>
            </div>
          )}

          <Button type="submit" className="w-full" disabled={isProcessing || !selectedFile || selectedFile.length === 0}>
            {isProcessing ? 'Processing...' : 'Start OCR Scan'}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
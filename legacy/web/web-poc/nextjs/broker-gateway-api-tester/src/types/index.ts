export interface Scan {
  id: string;
  fileName: string;
  uploadDate: string; // ISO string date
  status: 'Processing' | 'Completed' | 'Error';
  ocrText?: string;
  fileUrl?: string; // URL to the original image (can be a placeholder)
  fileSize: number; // in bytes
}
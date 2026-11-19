'use client';

import { useState, useEffect } from 'react';
import { v4 as uuidv4 } from 'uuid';
import Tesseract from 'tesseract.js';
import FileUploaderCard from '@/components/file-uploader-card';
import ScanListCard from '@/components/scan-list-card';
import type { Scan } from '@/types';

export default function OcrUploadPage() {
  const [scans, setScans] = useState<Scan[]>([]);
  const [isProcessingAny, setIsProcessingAny] = useState(false);

  const processFileAction = async (formData: FormData) => {
    const file = formData.get('file') as File;
    if (!file) {
      return { error: 'No file provided.' };
    }

    const scanId = uuidv4(); // Generate ID once and reuse
    const newScan: Scan = {
      id: scanId,
      fileName: file.name,
      uploadDate: new Date().toISOString(),
      status: 'Processing',
      fileSize: file.size,
    };

    setScans((prev) => {
      // Prevent duplicate scans with same ID
      const existingScan = prev.find(s => s.id === scanId);
      if (existingScan) {
        console.warn('Duplicate scan ID detected, skipping:', scanId);
        return prev;
      }
      return [newScan, ...prev];
    });
    setIsProcessingAny(true);

    try {
      const {
        data: { text },
      } = await Tesseract.recognize(file, 'eng');

      const completedScan: Scan = {
        ...newScan,
        status: 'Completed',
        ocrText: text,
        fileUrl: '/path/to/simulated/file.pdf', // This can be updated later
      };

      setScans((prev) =>
        prev.map((s) => (s.id === scanId ? completedScan : s))
      );
      return { scan: completedScan };
    } catch (error) {
      console.error('OCR processing failed:', error);

      const errorScan: Scan = { ...newScan, status: 'Error' };

      setScans((prev) =>
        prev.map((s) => (s.id === scanId ? errorScan : s))
      );
      return { error: 'OCR processing failed.' };
    } finally {
      setIsProcessingAny(false);
    }
  };

  useEffect(() => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('scans', JSON.stringify(scans));
    }
  }, [scans]);

  return (
    <div className="flex flex-col space-y-6 p-6">
      <h1 className="text-3xl font-bold">OCR Upload and Review</h1>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <FileUploaderCard
          onFileProcessed={(newScan) => {
            if (newScan) {
              setScans((prev) => [newScan, ...prev]);
            }
          }}
          isProcessing={isProcessingAny}
          setIsProcessing={setIsProcessingAny}
          processFileAction={processFileAction}
        />
        <ScanListCard scans={scans} isProcessingAny={isProcessingAny} />
      </div>
    </div>
  );
}
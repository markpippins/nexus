'use client';

import { useState, useEffect, use } from 'react';
import { notFound } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

// This is a mock function to fetch scan data. In a real app, you'd fetch this from your state management or an API.
const getScanById = (id: string, scans: any[]) => {
  return scans.find((scan) => scan.id === id);
};

export default function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [scan, setScan] = useState<any>(null);
  const [text, setText] = useState('');
  const [isSaved, setIsSaved] = useState(true);
  const [wordCount, setWordCount] = useState(0);
  const [charCount, setCharCount] = useState(0);

  useEffect(() => {
    // In a real app, you might use a global state or context to get the scans.
    // For this example, we'll assume the data is not available on direct navigation and guide the user back.
    // A more robust solution would involve fetching the data if it's not in the client-side state.
    if (typeof window !== 'undefined') {
        const storedScans = localStorage.getItem('scans');
        if(storedScans) {
            const scans = JSON.parse(storedScans);
            const foundScan = getScanById(id, scans);
            if (foundScan) {
                setScan(foundScan);
                setText(foundScan.ocrText || '');
            } else {
                // Handle case where scan is not found
            }
        }
    }
  }, [id]);

  useEffect(() => {
    const currentText = text;
    setCharCount(currentText.length);
    setWordCount(currentText.trim() ? currentText.trim().split(/\s+/).length : 0);
  }, [text]);

  const handleTextChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(event.target.value);
    setIsSaved(false);
  };

  const handleSave = () => {
    // Here you would implement actual saving logic, e.g., updating the state or sending to an API.
    setIsSaved(true);
    console.log('Document saved:', { filename: scan?.fileName, text });
    // You might want to update the scan in your global state here
  };

  const handleExport = () => {
    const blob = new Blob([text], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${scan?.fileName.replace(/\.[^/.]+$/, '')}_edited.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  if (!scan) {
    return (
        <div className="flex flex-col items-center justify-center h-screen bg-background">
            <Card className="w-full max-w-md p-6 text-center">
                <CardHeader>
                    <CardTitle className="text-2xl font-bold">Scan Not Found</CardTitle>
                    <CardDescription>The scan you are looking for does not exist or was not loaded.</CardDescription>
                </CardHeader>
                <CardContent>
                    <p className="mb-4">Please go back to the upload page and try again.</p>
                    <Button asChild>
                        <Link href="/ocr-upload">
                            <ArrowLeft className="mr-2 h-4 w-4" />
                            Back to Upload
                        </Link>
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
  }

  return (
    <div className="flex flex-col h-screen p-4 sm:p-6 lg:p-8">
      <header className="mb-4">
        <Card>
          <CardHeader>
            <div className="flex justify-between items-center flex-wrap gap-4">
              <div>
                <h1 className="text-2xl font-bold">Review and Edit</h1>
                <p className="text-muted-foreground">Editing: <strong>{scan.fileName}</strong></p>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" asChild>
                    <Link href="/ocr-upload">
                        <ArrowLeft className="mr-2 h-4 w-4" />
                        Back to Scans
                    </Link>
                </Button>
                <Button onClick={handleSave} disabled={isSaved}>
                  {isSaved ? 'Saved' : 'Save'}
                </Button>
                <Button onClick={handleExport} variant="secondary">
                  Export as TXT
                </Button>
              </div>
            </div>
          </CardHeader>
        </Card>
      </header>

      <div className="flex-grow flex flex-col">
        <Card className="flex-grow flex flex-col">
          <CardHeader className="border-b">
            <div className="flex justify-between items-center text-sm text-muted-foreground">
              <div className="flex gap-4">
                <span>Words: <strong>{wordCount}</strong></span>
                <span>Characters: <strong>{charCount}</strong></span>
              </div>
              <div>
                {!isSaved && <Badge variant="destructive">Unsaved Changes</Badge>}
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex-grow p-0">
            <Textarea
              className="h-full w-full resize-none border-0 focus:ring-0 focus:outline-none p-4 text-base"
              value={text}
              onChange={handleTextChange}
              placeholder="OCR text will appear here..."
            />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
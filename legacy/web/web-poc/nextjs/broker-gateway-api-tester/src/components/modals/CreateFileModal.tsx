'use client';
import { useState } from 'react';
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FilePlus, Lightbulb, Loader2 } from 'lucide-react';
import { smartNameSuggestions } from '@/ai/flows/smart-name-suggestions';
import type { FileSystemItem } from '@/lib/types';
import { Badge } from '../ui/badge';

interface CreateFileModalProps {
  alias: string;
  currentDirectoryContents: FileSystemItem[];
  onCreate: (name: string) => void;
  disabled?: boolean;
}

export function CreateFileModal({ alias, currentDirectoryContents, onCreate, disabled }: CreateFileModalProps) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [loadingSuggestions, setLoadingSuggestions] = useState(false);

  const getSuggestions = async () => {
    setLoadingSuggestions(true);
    setSuggestions([]);
    try {
      const result = await smartNameSuggestions({
        alias,
        directoryContents: currentDirectoryContents.map(item => item.name),
        operationType: 'file'
      });
      if(result.suggestions) {
        setSuggestions(result.suggestions);
      }
    } catch (error) {
      console.error("Failed to get suggestions:", error);
    } finally {
      setLoadingSuggestions(false);
    }
  };
  
  const handleSubmit = () => {
    if (name) {
      onCreate(name);
      setName('');
      setSuggestions([]);
      setOpen(false);
    }
  };

  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen) {
      setName('');
      setSuggestions([]);
    }
    setOpen(isOpen);
  };
  
  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button variant="outline" disabled={disabled}>
          <FilePlus className="mr-2 h-4 w-4" />
          New File
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Create New File</DialogTitle>
          <DialogDescription>Enter a name for your new file. Click suggest for AI-powered ideas.</DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-4">
          <div className="grid grid-cols-4 items-center gap-4">
            <Label htmlFor="name" className="text-right">Name</Label>
            <Input id="name" value={name} onChange={(e) => setName(e.target.value)} className="col-span-3" placeholder="e.g., notes.txt" />
          </div>
          {suggestions.length > 0 && (
            <div className="grid grid-cols-4 items-center gap-4">
                <div/>
                <div className="col-span-3 flex flex-wrap gap-2">
                {suggestions.map(s => (
                    <Badge key={s} variant="secondary" className="cursor-pointer hover:bg-accent" onClick={() => setName(s)}>{s}</Badge>
                ))}
                </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={getSuggestions} disabled={loadingSuggestions}>
            {loadingSuggestions ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Lightbulb className="mr-2 h-4 w-4" />}
            Suggest
          </Button>
          <Button onClick={handleSubmit} disabled={!name}>Create File</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

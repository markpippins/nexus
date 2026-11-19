'use client';

import { useState, useEffect, useCallback } from 'react';
import { useToast } from '@/hooks/use-toast';
import { listDirectory, createDirectory, createFile, deleteItem, renameItem } from '../lib/api';
import type { FileSystemItem } from '../lib/types';
import useLocalStorage from './useLocalStorage';

export function useFileFlow() {
  const [alias, setAlias] = useLocalStorage('fileflow-alias', '');
  const [path, setPath] = useState<string[]>([]);
  const [items, setItems] = useState<FileSystemItem[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();

  const fetchItems = useCallback(async (currentAlias: string, currentPath: string[]) => {
    if (!currentAlias) {
      setItems([]);
      setPath([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    const response = await listDirectory(currentAlias, currentPath);
    setLoading(false);

    if (response.error) {
      toast({ title: 'Error loading directory', description: response.error, variant: 'destructive' });
      setItems([]);
    } else if (response.items && response.path) {
      const sortedItems = response.items.sort((a, b) => {
        if (a.type === 'directory' && b.type === 'file') return -1;
        if (a.type === 'file' && b.type === 'directory') return 1;
        return a.name.localeCompare(b.name);
      });
      setItems(sortedItems);
      setPath(response.path);
    } else {
        toast({ title: 'Unexpected API response', description: 'The server response was not in the expected format.', variant: 'destructive' });
        setItems([]);
    }
  }, [toast]);

  const refresh = useCallback(() => {
    fetchItems(alias, path);
  }, [alias, path, fetchItems]);

  useEffect(() => {
    fetchItems(alias, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alias]);

  const navigateTo = (newPath: string[]) => {
    fetchItems(alias, newPath);
  };

  const handleCreateDirectory = async (name: string) => {
    setLoading(true);
    const res = await createDirectory(alias, path, name);
    if(res.error) {
      toast({ title: `Failed to create directory '${name}'`, description: res.error, variant: 'destructive'});
    } else {
      toast({ title: `Directory '${name}' created`, description: res.message || res.status });
      refresh();
    }
    setLoading(false);
  };

  const handleCreateFile = async (name: string) => {
    setLoading(true);
    const res = await createFile(alias, path, name);
    if(res.error) {
      toast({ title: `Failed to create file '${name}'`, description: res.error, variant: 'destructive'});
    } else {
      toast({ title: `File '${name}' created`, description: res.message || res.status });
      refresh();
    }
    setLoading(false);
  };

  const handleDeleteItem = async (name: string, type: 'file' | 'directory') => {
    setLoading(true);
    const res = await deleteItem(alias, path, name, type);
    if(res.error) {
      toast({ title: `Failed to delete '${name}'`, description: res.error, variant: 'destructive'});
    } else {
      toast({ title: `'${name}' deleted`, description: res.message || res.status });
      refresh();
    }
    setLoading(false);
  };

  const handleRenameItem = async (oldName: string, newName: string) => {
    setLoading(true);
    const res = await renameItem(alias, path, oldName, newName);
    if(res.error) {
      toast({ title: `Failed to rename '${oldName}'`, description: res.error, variant: 'destructive'});
    } else {
      toast({ title: `'${oldName}' renamed to '${newName}'`, description: res.message || res.status });
      refresh();
    }
    setLoading(false);
  };

  return {
    alias,
    setAlias,
    path,
    items,
    loading,
    refresh,
    navigateTo,
    handleCreateDirectory,
    handleCreateFile,
    handleDeleteItem,
    handleRenameItem,
  };
}

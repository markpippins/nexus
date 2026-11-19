'use client';

import { useState, useEffect } from 'react';
import { File, Folder, MoreVertical, Edit, Trash2, RefreshCw } from 'lucide-react';
import type { FileSystemItem } from '@/lib/types';
import { useFileFlow } from '../hooks/useFileFlow';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';
import { Breadcrumbs } from './Breadcrumbs';
import { CreateFolderModal } from './modals/CreateFolderModal';
import { CreateFileModal } from './modals/CreateFileModal';
import { RenameModal } from './modals/RenameModal';
import { DeleteModal } from './modals/DeleteModal';
import { Skeleton } from './ui/skeleton';
import { Input } from './ui/input';
import { Label } from './ui/label';

export function AdminFileBrowser() {
    const {
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
    } = useFileFlow();
    
    const [renameModalOpen, setRenameModalOpen] = useState(false);
    const [deleteModalOpen, setDeleteModalOpen] = useState(false);
    const [selectedItem, setSelectedItem] = useState<FileSystemItem | null>(null);
    const [isClient, setIsClient] = useState(false);

    useEffect(() => {
        setIsClient(true);
    }, []);

    const handleRenameClick = (item: FileSystemItem) => {
        setSelectedItem(item);
        setRenameModalOpen(true);
    };

    const handleDeleteClick = (item: FileSystemItem) => {
        setSelectedItem(item);
        setDeleteModalOpen(true);
    };

    const onRenameSubmit = (newName: string) => {
        if (selectedItem) {
            handleRenameItem(selectedItem.name, newName);
        }
    };

    const onDeleteSubmit = () => {
        if (selectedItem) {
            handleDeleteItem(selectedItem.name, selectedItem.type);
        }
    };

    const handleItemClick = (item: FileSystemItem) => {
        if (item.type === 'directory') {
            navigateTo([...path, item.name]);
        }
    };

    const renderItem = (item: FileSystemItem) => (
        <TableRow 
            key={item.name} 
            className="group hover:bg-accent/50 cursor-pointer" 
            onDoubleClick={() => handleItemClick(item)}
        >
            <TableCell onClick={() => handleItemClick(item)} className="p-1">
                <div className="flex items-center gap-1">
                    {item.type === 'directory' ? <Folder className="h-5 w-5 text-primary" /> : <File className="h-5 w-5 text-muted-foreground" />}
                    <span className="font-medium">{item.name}</span>
                </div>
            </TableCell>
            <TableCell className="text-right p-1">
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="opacity-0 group-hover:opacity-100 transition-opacity h-8 w-8">
                            <MoreVertical className="h-4 w-4" />
                            <span className="sr-only">Actions for {item.name}</span>
                        </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => handleRenameClick(item)}>
                            <Edit className="mr-2 h-4 w-4" /> Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => handleDeleteClick(item)} className="text-destructive focus:text-destructive focus:bg-destructive/10">
                            <Trash2 className="mr-2 h-4 w-4" /> Delete
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </TableCell>
        </TableRow>
    );

    return (
        <div className="fixed top-20 left-32 right-32 bottom-8 flex flex-col bg-background text-foreground overflow-hidden">
            <main className="flex-1 m-0 p-0">
                <Card className="h-full w-full flex flex-col overflow-hidden m-0 border-0 rounded-none shadow-none">
                    <CardHeader className="p-0 m-0">
                        <div className="flex justify-between items-start md:items-center gap-1 flex-col md:flex-row p-1">
                            {/* <div className="space-y-1 m-0">
                                <CardTitle className="text-xl m-0">Files</CardTitle>
                                <Breadcrumbs path={path} onNavigate={navigateTo} />
                            </div> */}
                            <Breadcrumbs path={path} onNavigate={navigateTo} />
                            <div className="flex gap-1">
                                <Button variant="ghost" size="icon" onClick={refresh} disabled={loading || !alias}>
                                    <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
                                    <span className="sr-only">Refresh</span>
                                </Button>
                                <CreateFileModal alias={alias} currentDirectoryContents={items} onCreate={handleCreateFile} disabled={!isClient || !alias} />
                                <CreateFolderModal alias={alias} currentDirectoryContents={items} onCreate={handleCreateDirectory} disabled={!isClient || !alias} />
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="flex-1 overflow-hidden p-0 m-0">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="p-1">Name</TableHead>
                                    <TableHead className="text-right w-12 p-1"></TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {loading || !isClient ? (
                                    [...Array(5)].map((_, i) => (
                                        <TableRow key={i}>
                                            <TableCell className="p-1">
                                                <div className="flex items-center gap-1">
                                                    <Skeleton className="h-5 w-5" />
                                                    <Skeleton className="h-5 w-48" />
                                                </div>
                                            </TableCell>
                                            <TableCell className="p-1"></TableCell>
                                        </TableRow>
                                    ))
                                ) : items.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={2} className="text-center text-muted-foreground h-48 p-1">
                                            {alias ? 'This folder is empty.' : 'Please enter an alias to start exploring.'}
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    items.map(renderItem)
                                )}
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>
            </main>
            <RenameModal isOpen={renameModalOpen} onOpenChange={setRenameModalOpen} itemName={selectedItem?.name || ''} onRename={onRenameSubmit} />
            <DeleteModal isOpen={deleteModalOpen} onOpenChange={setDeleteModalOpen} item={selectedItem} onDelete={onDeleteSubmit} />
        </div>
    );
}

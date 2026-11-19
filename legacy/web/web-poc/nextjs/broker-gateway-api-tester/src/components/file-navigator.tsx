"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Folder,
  File,
  Loader2,
  FolderPlus,
  FilePlus,
  Trash2,
  Edit,
  ArrowUp,
  RefreshCw,
  Terminal,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { useToast } from "@/hooks/use-toast";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { cn } from "@/lib/utils";
import { useAuth } from "@/contexts/auth-context";
import { useBrokerApi } from "@/hooks/use-broker-api";
import dummyFileSystem from "@/lib/dummy-fs.json";

type FileSystemItem = {
  name: string;
  type: "file" | "directory";
  size: number;
  last_modified: string;
  items?: FileSystemItem[];
};

type ApiResponse = {
  items: FileSystemItem[];
};

// Helper function to navigate the dummy FS
const getItemsFromDummyFs = (
  fs: FileSystemItem,
  path: string[]
): FileSystemItem[] | null => {
  let currentLevel: FileSystemItem | undefined = fs;
  for (const part of path) {
    if (currentLevel?.type === "directory" && currentLevel.items) {
      currentLevel = currentLevel.items.find((item) => item.name === part);
    } else {
      return null;
    }
  }
  return currentLevel?.items || null;
};

export function FileNavigator() {
  const { isDebugMode } = useAuth();
  const { callBroker } = useBrokerApi();
  const [dummyFs, setDummyFs] = useState<FileSystemItem>(dummyFileSystem as FileSystemItem);

  const { user } = useAuth();
  const [token] = useState(user?.token || ""); // Assuming token is stored in user object
  const [path, setPath] = useState<string[]>(["users", user?.alias || "default-user", "home"]);
  const [items, setItems] = useState<FileSystemItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [newItemName, setNewItemName] = useState("");
  const [renameData, setRenameData] = useState<{
    oldName: string;
    newName: string;
  } | null>(null);

  const { toast } = useToast();

  const callFsApi = useCallback(
    async (
      operation: string,
      extraParams: Record<string, any> = {}
    ): Promise<any> => {
      if (isDebugMode) {
        setLoading(true);
        // Simulate network delay
        await new Promise(resolve => setTimeout(resolve, 300));
        try {
          // The root of our dummy FS is the single item in the `items` array
          let currentItems = getItemsFromDummyFs(dummyFs, path.slice(0, -1));
          let currentFolder = path.length > 0
            ? currentItems?.find(i => i.name === path[path.length-1] && i.type === 'directory')
            : dummyFs;

          if (!currentFolder || currentFolder.type !== 'directory') {
             currentFolder = dummyFs;
          }

          if (!currentFolder.items) {
            currentFolder.items = [];
          }

          let targetPathItems = getItemsFromDummyFs(dummyFs, path);

          switch (operation) {
            case "ls": {
              const itemsWithMetadata = (targetPathItems || []).map(
                (item) => ({
                  ...item,
                  size:
                    item.size ??
                    (item.type === "file"
                      ? Math.floor(Math.random() * 10000)
                      : 0),
                  last_modified:
                    item.last_modified ?? new Date().toISOString(),
                })
              );
              return { items: itemsWithMetadata };
            }
            case "mkdir": {
              const newDirName = extraParams.path[extraParams.path.length - 1];
              if (!targetPathItems?.find(i => i.name === newDirName)) {
                targetPathItems?.push({ name: newDirName, type: 'directory', items: [], size: 0, last_modified: new Date().toISOString() });
              }
              break;
            }
            case "newfile": {
              if (!targetPathItems?.find(i => i.name === extraParams.filename)) {
                targetPathItems?.push({ name: extraParams.filename, type: 'file', size: 0, last_modified: new Date().toISOString() });
              }
              break;
            }
            case "rmdir":
            case "deletefile": {
              const nameToDelete = (operation === 'rmdir' ? extraParams.path[extraParams.path.length-1] : extraParams.filename);
              const index = targetPathItems?.findIndex(i => i.name === nameToDelete);
              if (index !== undefined && index > -1) {
                targetPathItems?.splice(index, 1);
              }
              break;
            }
            case "rename": {
              const oldName = extraParams.path[extraParams.path.length-1];
              const item = targetPathItems?.find(i => i.name === oldName);
              if (item) {
                item.name = extraParams.new_name;
              }
              break;
            }
            default:
              throw new Error(`Unsupported debug operation: ${operation}`);
          }
          setDummyFs({ ...dummyFs }); // Trigger re-render
          return { items: targetPathItems };
        } catch (e) {
           const errorMessage = e instanceof Error ? e.message : "An unknown error occurred";
           toast({ variant: "destructive", title: "Debug Operation Failed", description: errorMessage });
           return null;
        } finally {
            setLoading(false);
        }
      }

      setLoading(true);
      try {
        // Use the broker API with dynamic URL
        const serviceOperation = operation === "ls" ? "listFiles" :
                                operation === "mkdir" ? "createDirectory" :
                                operation === "newfile" ? "createFile" :
                                operation === "rmdir" ? "removeDirectory" :
                                operation === "deletefile" ? "deleteFile" :
                                operation === "rename" ? "rename" : operation;

        const result = await callBroker("restFsService", serviceOperation, { token, path, ...extraParams });

        if (result.error) {
          throw new Error(result.error);
        }

        return result;
      } catch (error) {
        const errorMessage =
          error instanceof Error ? error.message : "An unknown error occurred";
        toast({
          variant: "destructive",
          title: "Operation Failed",
          description: errorMessage,
        });
        return null;
      } finally {
        setLoading(false);
      }
    },
    [token, path, toast, isDebugMode, dummyFs, callBroker]
  );

  const listFiles = useCallback(async () => {
    const data: ApiResponse | null = await callFsApi("ls");
    if (data && Array.isArray(data.items)) {
      // Handle both object items (debug mode) and string items (non-debug mode)
      const processedItems = data.items.map(item => {
        if (typeof item === 'string') {
          // For non-debug mode, convert string items to FileSystemItem objects
          // Determine if it's a file or directory based on presence of extension
          const hasExtension = (item as string).includes('.');
          return {
            name: item,
            type: hasExtension ? 'file' : 'directory',
            size: 0,
            last_modified: new Date().toISOString()
          } as FileSystemItem;
        }
        return item;
      });
      setItems(processedItems);
    } else {
      setItems([]);
    }
  }, [callFsApi]);

  useEffect(() => {
    listFiles();
  }, [path, listFiles]);

  const handleNavigate = (name: string) => {
    setPath([...path, name]);
  };

  const handleGoUp = () => {
    // Prevent navigation above /users/alias/home
    if (path.length > 3) {
      setPath(path.slice(0, -1));
    } else {
      toast({
        title: "Navigation restricted",
        description: "Cannot navigate above your home directory",
      });
    }
  };

  const handleCreate = async (type: "file" | "directory") => {
    if (!newItemName) {
      toast({
        variant: "destructive",
        title: "Error",
        description: "Name cannot be empty.",
      });
      return;
    }
    const operation = type === "directory" ? "mkdir" : "newfile";
    const params =
      type === "directory"
        ? { path: [...path, newItemName] }
        : { filename: newItemName };

    await callFsApi(operation, params);
    setNewItemName("");
    await listFiles();
  };

  const handleDelete = async (name: string, type: "file" | "directory") => {
    const isDirectory = type === "directory";
    const operation = isDirectory ? "rmdir" : "deletefile";
    const params = isDirectory
      ? { path: [...path, name] }
      : { filename: name };

    await callFsApi(operation, params);
    await listFiles();
  };

  const handleRename = async () => {
    if (!renameData || !renameData.newName) {
      toast({
        variant: "destructive",
        title: "Error",
        description: "New name cannot be empty.",
      });
      return;
    }
    const params = {
      path: [...path, renameData.oldName],
      new_name: renameData.newName,
    };
    await callFsApi("rename", params);
    setRenameData(null);
    await listFiles();
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Folder className="h-6 w-6" />
            File System {isDebugMode && "(Debug Mode)"}
          </span>
          <div className="flex items-center space-x-2">
            <Button variant="outline" size="icon" onClick={listFiles} disabled={loading}>
              <RefreshCw className="h-4 w-4" />
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="icon" disabled={loading}>
                  <FolderPlus className="h-4 w-4" />
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Create New Folder</AlertDialogTitle>
                  <AlertDialogDescription>
                    Enter a name for the new folder.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <Input
                  placeholder="Folder name"
                  value={newItemName}
                  onChange={(e) => setNewItemName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate('directory')}
                />
                <AlertDialogFooter>
                  <AlertDialogCancel onClick={() => setNewItemName("")}>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => handleCreate("directory")}>Create</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" size="icon" disabled={loading}>
                  <FilePlus className="h-4 w-4" />
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Create New File</AlertDialogTitle>
                  <AlertDialogDescription>
                    Enter a name for the new file.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <Input
                  placeholder="File name"
                  value={newItemName}
                  onChange={(e) => setNewItemName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreate('file')}
                />
                <AlertDialogFooter>
                  <AlertDialogCancel onClick={() => setNewItemName("")}>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => handleCreate("file")}>Create</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </CardTitle>
        <CardDescription>
          Current path: /
          {path.length > 0 && (
            <>
              {path[0]}/
              {path.length > 1 && (
                <>
                  <span className="text-primary">{path[1]}</span>/
                  {path.slice(2).map((p, i) => (
                    <span key={`${p}-${i}`}>{p}/</span>
                  ))}
                </>
              )}
            </>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="border rounded-md min-h-[550px] overflow-auto max-h-[550px]">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Size</TableHead>
                <TableHead>Last Modified</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {path.length > 0 && (
                <TableRow
                  key="go-up-row"
                  className="cursor-pointer hover:bg-muted/50"
                  onClick={handleGoUp}
                >
                  <TableCell colSpan={5} className="flex items-center gap-2">
                    <ArrowUp className="h-4 w-4" /> Go up
                  </TableCell>
                </TableRow>
              )}
              {loading ? (
                <TableRow key="loading-row">
                  <TableCell colSpan={5} className="text-center h-24">
                    <div className="flex justify-center items-center gap-2 text-muted-foreground">
                      <Loader2 className="h-6 w-6 animate-spin" />
                      <span>Loading...</span>
                    </div>
                  </TableCell>
                </TableRow>
              ) : items.length > 0 ? (
                items.map((item) => (
                  <TableRow key={item.name}>
                    <TableCell
                      className={cn(
                        "font-medium flex items-center gap-2",
                        item.type === "directory" &&
                          "cursor-pointer hover:underline"
                      )}
                      onClick={() =>
                        item.type === "directory" && handleNavigate(item.name)
                      }
                    >
                      {item.type === "directory" ? (
                        <Folder className="h-4 w-4 text-primary" key={`folder-icon-${item.name}`} />
                      ) : (
                        <File className="h-4 w-4 text-muted-foreground" key={`file-icon-${item.name}`} />
                      )}
                      {item.name}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {item.type}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {item.type === "directory" ? "-" : item.size ?? 0}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {item.last_modified
                        ? new Date(item.last_modified).toLocaleString()
                        : "-"}
                    </TableCell>
                    <TableCell className="text-right">
                      <AlertDialog key={`rename-dialog-${item.name}`}>
                        <AlertDialogTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() =>
                              setRenameData({
                                oldName: item.name,
                                newName: item.name,
                              })
                            }
                          >
                            <Edit className="h-4 w-4" />
                          </Button>
                        </AlertDialogTrigger>
                      </AlertDialog>
                      <AlertDialog key={`delete-dialog-${item.name}`}>
                        <AlertDialogTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="text-destructive hover:text-destructive"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>
                              Are you sure?
                            </AlertDialogTitle>
                            <AlertDialogDescription>
                              This action cannot be undone. This will
                              permanently delete "{item.name}".
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              onClick={() =>
                                handleDelete(item.name, item.type)
                              }
                            >
                              Delete
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow key="empty-directory-row">
                  <TableCell colSpan={5} className="h-24 text-center">
                    <div className="text-center text-muted-foreground">
                      <Terminal className="mx-auto h-12 w-12 mb-2" />
                      <p>This directory is empty.</p>
                    </div>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
        <AlertDialog
          open={!!renameData}
          onOpenChange={(open) => !open && setRenameData(null)}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Rename Item</AlertDialogTitle>
              <AlertDialogDescription>
                Enter a new name for "{renameData?.oldName}".
              </AlertDialogDescription>
            </AlertDialogHeader>
            <Input
              value={renameData?.newName || ""}
              onChange={(e) =>
                renameData &&
                setRenameData({ ...renameData, newName: e.target.value })
              }
              onKeyDown={(e) => e.key === "Enter" && handleRename()}
            />
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={handleRename}>
                Rename
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </CardContent>
    </Card>
  );
}

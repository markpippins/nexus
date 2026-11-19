'use client';

import Link from 'next/link';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Eye, FileText, AlertTriangle, RefreshCw } from 'lucide-react';
import type { Scan } from '@/types';
import { format } from 'date-fns';

interface ScanTableProps {
  scans: Scan[];
}

function formatBytes(bytes: number, decimals = 2) {
  if (bytes === 0) return '0 Bytes';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

export default function ScanTable({ scans }: ScanTableProps) {
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[50px] hidden sm:table-cell"></TableHead>
            <TableHead>File Name</TableHead>
            <TableHead className="hidden md:table-cell">Size</TableHead>
            <TableHead className="hidden lg:table-cell">Upload Date</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {scans.map((scan, index) => (
            <TableRow key={`${scan.id}-${index}`}>
              <TableCell className="hidden sm:table-cell">
                {scan.status === 'Processing' ? (
                  <RefreshCw className="h-5 w-5 text-muted-foreground animate-spin" />
                ) : scan.status === 'Error' ? (
                  <AlertTriangle className="h-5 w-5 text-destructive" />
                ) : (
                  <FileText className="h-5 w-5 text-primary" />
                )}
              </TableCell>
              <TableCell className="font-medium truncate max-w-xs">{scan.fileName}</TableCell>
              <TableCell className="hidden md:table-cell">{formatBytes(scan.fileSize)}</TableCell>
              <TableCell className="hidden lg:table-cell">{format(new Date(scan.uploadDate), 'PPpp')}</TableCell>
              <TableCell>
                <Badge
                  variant={
                    scan.status === 'Completed'
                      ? 'default'
                      : scan.status === 'Processing'
                      ? 'secondary'
                      : 'destructive'
                  }
                  className={scan.status === 'Completed' ? 'bg-accent text-accent-foreground' : ''}
                >
                  {scan.status}
                </Badge>
              </TableCell>
              <TableCell className="text-right">
                {scan.status === 'Completed' && (
                  <Button variant="outline" size="sm" asChild>
                    <Link href={`/review/${scan.id}`}>
                      <Eye className="mr-2 h-4 w-4" /> Review
                    </Link>
                  </Button>
                )}
                 {scan.status === 'Processing' && (
                  <Button variant="outline" size="sm" disabled>
                    <RefreshCw className="mr-2 h-4 w-4 animate-spin" /> Processing
                  </Button>
                )}
                {scan.status === 'Error' && (
                   <Button variant="ghost" size="sm" className="text-destructive hover:bg-destructive/10" disabled>
                     <AlertTriangle className="mr-2 h-4 w-4" /> Error
                   </Button>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
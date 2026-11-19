import React, { useState } from 'react';
import { Search as SearchIcon, FileCode, ArrowRight } from 'lucide-react';
import { FileItem } from '../types';

interface SearchPanelProps {
  files: FileItem[];
  onSelectFile: (file: FileItem) => void;
}

interface SearchResult {
  file: FileItem;
  lineNumber: number;
  lineContent: string;
}

export const SearchPanel: React.FC<SearchPanelProps> = ({ files, onSelectFile }) => {
  const [searchTerm, setSearchTerm] = useState('');

  const getAllFiles = (items: FileItem[]): FileItem[] => {
    let result: FileItem[] = [];
    for (const item of items) {
      if (item.type === 'file' && item.content) {
        result.push(item);
      }
      if (item.children) {
        result = result.concat(getAllFiles(item.children));
      }
    }
    return result;
  };

  const allFiles = getAllFiles(files);

  const results: SearchResult[] = [];
  if (searchTerm.trim().length >= 2) {
    const query = searchTerm.toLowerCase();
    for (const file of allFiles) {
      if (file.content) {
        const lines = file.content.split('\n');
        lines.forEach((line, index) => {
          if (line.toLowerCase().includes(query)) {
            results.push({
              file,
              lineNumber: index + 1,
              lineContent: line.trim(),
            });
          }
        });
      }
    }
  }

  return (
    <div className="flex flex-col h-full bg-[#252526] text-[#cccccc] text-sm">
      <div className="flex items-center px-3 py-2 border-b border-[#333333] tracking-wide font-semibold text-[11px] uppercase text-[#bbbbbb]">
        <span>SEARCH WORKSPACE</span>
      </div>

      <div className="p-3 border-b border-[#333333]">
        <div className="relative flex items-center">
          <input
            type="text"
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            placeholder="Search text in workspace..."
            className="w-full bg-[#1e1e1e] border border-[#3c3c3c] focus:border-[#007acc] text-white text-sm pl-8 pr-3 py-1.5 rounded outline-none"
          />
          <SearchIcon className="w-4 h-4 text-[#858585] absolute left-2.5 pointer-events-none" />
        </div>
        <div className="text-[10px] text-[#858585] mt-1.5 flex justify-between">
          <span>{searchTerm ? `${results.length} results found` : 'Type at least 2 chars'}</span>
          <span>{allFiles.length} files scanned</span>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 custom-scrollbar">
        {results.length === 0 && searchTerm.trim().length >= 2 && (
          <div className="p-4 text-center text-[#858585] italic">
            No matching code lines found.
          </div>
        )}

        {results.map((res, idx) => (
          <div
            key={idx}
            onClick={() => onSelectFile(res.file)}
            className="p-2 mb-1.5 rounded hover:bg-[#2a2d2e] cursor-pointer border border-transparent hover:border-[#3c3c3c] transition-all group"
          >
            <div className="flex items-center justify-between text-[#3794ff] font-medium mb-1">
              <div className="flex items-center gap-1.5 truncate">
                <FileCode className="w-3.5 h-3.5 text-blue-400 shrink-0" />
                <span className="truncate">{res.file.path}</span>
              </div>
              <span className="text-[10px] bg-[#333333] px-1.5 py-0.5 rounded text-[#aaaaaa]">
                L{res.lineNumber}
              </span>
            </div>
            <div className="font-mono text-[11px] text-[#cccccc] bg-[#1e1e1e] p-1.5 rounded truncate border border-[#2d2d2d] group-hover:border-[#007acc]/40">
              {res.lineContent}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

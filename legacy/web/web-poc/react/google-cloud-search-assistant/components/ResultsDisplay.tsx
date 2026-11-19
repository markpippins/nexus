
import React from 'react';
import { SearchResult } from '../types';
import DownloadIcon from './icons/DownloadIcon';

interface ResultsDisplayProps {
  result: SearchResult;
}

const ResultsDisplay: React.FC<ResultsDisplayProps> = ({ result }) => {
  const handleDownload = () => {
    const jsonString = `data:text/json;charset=utf-8,${encodeURIComponent(
      JSON.stringify(result.rawResponse, null, 2)
    )}`;
    const link = document.createElement("a");
    link.href = jsonString;
    link.download = "search_results.json";
    link.click();
  };

  return (
    <div className="w-full max-w-3xl mx-auto mt-8 bg-white dark:bg-gray-800 rounded-lg shadow-xl overflow-hidden animate-fade-in">
      <div className="p-6 md:p-8">
        <div className="flex justify-between items-start mb-6">
          <h2 className="text-2xl font-bold text-brand-text-primary dark:text-gray-100">Search Results</h2>
          <button
            onClick={handleDownload}
            className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-brand-primary dark:text-sky-400 bg-brand-secondary dark:bg-gray-700 rounded-full hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
            title="Download full response as JSON"
          >
            <DownloadIcon className="w-5 h-5" />
            <span>Download JSON</span>
          </button>
        </div>
        
        <div className="prose prose-lg dark:prose-invert max-w-none text-brand-text-secondary dark:text-gray-300">
          <p>{result.text}</p>
        </div>

        {result.sources.length > 0 && (
          <div className="mt-8">
            <h3 className="text-xl font-semibold text-brand-text-primary dark:text-gray-100 mb-4">Sources</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {result.sources.map((source, index) => (
                source.web && (
                    <a
                      key={index}
                      href={source.web.uri}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block p-4 bg-gray-50 dark:bg-gray-700 rounded-lg hover:shadow-md hover:bg-gray-100 dark:hover:bg-gray-600 transition-all duration-300"
                    >
                      <p className="font-semibold text-brand-primary dark:text-sky-400 truncate">{source.web.title}</p>
                      <p className="text-sm text-brand-text-secondary dark:text-gray-400 truncate">{source.web.uri}</p>
                    </a>
                )
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ResultsDisplay;

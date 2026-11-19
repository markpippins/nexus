
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

  if (!result.items || result.items.length === 0) {
    return (
      <div className="w-full max-w-3xl mx-auto mt-8 bg-white dark:bg-gray-800 rounded-lg shadow-xl p-6 md:p-8 text-center animate-fade-in">
        <h2 className="text-xl font-semibold text-brand-text-primary dark:text-gray-100">No Results Found</h2>
        <p className="mt-2 text-brand-text-secondary dark:text-gray-400">Try refining your search query.</p>
      </div>
    );
  }


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
        
        <div className="space-y-6">
          {result.items.map((item, index) => (
            <div key={index} className="p-4 bg-gray-50 dark:bg-gray-700/50 rounded-lg transition-shadow duration-300 hover:shadow-md">
               <a
                href={item.link}
                target="_blank"
                rel="noopener noreferrer"
                className="text-lg font-semibold text-brand-primary hover:underline dark:text-sky-400"
              >
                {item.title}
              </a>
              <p className="text-sm text-green-700 dark:text-green-500 mt-1 truncate">{item.link}</p>
              <p className="mt-2 text-brand-text-secondary dark:text-gray-300">
                {item.snippet}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default ResultsDisplay;

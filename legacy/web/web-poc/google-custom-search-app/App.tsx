import React, { useState, useCallback } from 'react';
import { performGoogleSearch } from './services/googleSearchService';
import { SearchResult } from './types';
import SearchForm from './components/SearchForm';
import ResultsDisplay from './components/ResultsDisplay';
import Spinner from './components/Spinner';

const ConfigForm: React.FC<{ onSave: (apiKey: string, searchEngineId: string) => void }> = ({ onSave }) => {
  const [apiKey, setApiKey] = useState('');
  const [searchEngineId, setSearchEngineId] = useState('');

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (apiKey.trim() && searchEngineId.trim()) {
      onSave(apiKey.trim(), searchEngineId.trim());
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 flex flex-col justify-center items-center p-4">
      <div className="w-full max-w-md mx-auto bg-white dark:bg-gray-800 rounded-xl shadow-2xl p-8">
        <h1 className="text-2xl font-bold text-center text-brand-text-primary dark:text-gray-100 mb-2">
          Configuration Required
        </h1>
        <p className="text-center text-brand-text-secondary dark:text-gray-400 mb-6">
          Please provide your Google Search API Key and Programmable Search Engine ID to continue.
        </p>
        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label htmlFor="search-api-key" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Search API Key
            </label>
            <input
              type="text"
              id="search-api-key"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter your API Key here"
              className="mt-1 block w-full px-4 py-3 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm placeholder-gray-400 focus:outline-none focus:ring-brand-primary focus:border-brand-primary sm:text-sm text-brand-text-primary dark:text-gray-200"
              required
              aria-describedby="api-key-help"
            />
          </div>
          <div>
            <label htmlFor="search-engine-id" className="block text-sm font-medium text-gray-700 dark:text-gray-300">
              Search Engine ID
            </label>
            <input
              type="text"
              id="search-engine-id"
              value={searchEngineId}
              onChange={(e) => setSearchEngineId(e.target.value)}
              placeholder="Enter your ID here"
              className="mt-1 block w-full px-4 py-3 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-md shadow-sm placeholder-gray-400 focus:outline-none focus:ring-brand-primary focus:border-brand-primary sm:text-sm text-brand-text-primary dark:text-gray-200"
              required
              aria-describedby="id-help"
            />
            <p className="mt-2 text-xs text-gray-500" id="id-help">
              You can get your ID from the{' '}
              <a 
                href="https://programmablesearchengine.google.com/controlpanel/all" 
                target="_blank" 
                rel="noopener noreferrer" 
                className="font-medium text-brand-primary hover:underline"
              >
                Control Panel
              </a>.
            </p>
          </div>

          <button
            type="submit"
            className="w-full flex justify-center py-3 px-4 border border-transparent rounded-full shadow-sm text-sm font-medium text-white bg-brand-primary hover:bg-blue-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-brand-primary transition-colors disabled:bg-gray-400"
            disabled={!apiKey.trim() || !searchEngineId.trim()}
          >
            Save and Continue
          </button>
        </form>
      </div>
    </div>
  );
};


const App: React.FC = () => {
  const [searchApiKey, setSearchApiKey] = useState<string | null>(process.env.SEARCH_API_KEY || null);
  const [searchEngineId, setSearchEngineId] = useState<string | null>(process.env.SEARCH_ENGINE_ID || null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = useCallback(async (query: string) => {
    if (!searchApiKey || !searchEngineId) {
        setError("Search API Key or Search Engine ID is not configured. Please provide them to start searching.");
        return;
    }
    setIsLoading(true);
    setResult(null);
    setError(null);
    try {
      const searchResult = await performGoogleSearch(query, searchEngineId, searchApiKey);
      setResult(searchResult);
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("An unknown error occurred.");
      }
    } finally {
      setIsLoading(false);
    }
  }, [searchApiKey, searchEngineId]);
  
  const handleSave = (apiKey: string, engineId: string) => {
      setSearchApiKey(apiKey);
      setSearchEngineId(engineId);
      setError(null); // Clear any previous errors
  };

  if (!searchApiKey || !searchEngineId) {
      return (
          <ConfigForm onSave={handleSave} />
      );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 text-brand-text-primary dark:text-gray-200 font-sans p-4 sm:p-6 lg:p-8">
      <main className="container mx-auto">
        <header className="text-center my-8 md:my-12">
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-blue-500 to-teal-400">
            Mark's Custom Search Shit
          </h1>
          <p className="mt-4 text-lg text-brand-text-secondary dark:text-gray-400 max-w-2xl mx-auto">
            Leverage Google's Custom Search API to get targeted search results and export them for local processing.
          </p>
        </header>

        <section className="mt-8">
          <SearchForm onSearch={handleSearch} isLoading={isLoading} />
        </section>

        <section className="mt-8">
          {isLoading && <Spinner />}
          {error && (
            <div className="max-w-3xl mx-auto p-4 bg-red-100 dark:bg-red-900/30 border border-red-400 dark:border-red-600 text-red-700 dark:text-red-300 rounded-lg text-center">
              <p className="font-semibold">Error</p>
              <p>{error}</p>
            </div>
          )}
          {result && <ResultsDisplay result={result} />}
        </section>
        
        <footer className="text-center mt-12 py-4">
            <p className="text-sm text-gray-500 dark:text-gray-400">Powered by Google Custom Search API</p>
        </footer>
      </main>
    </div>
  );
};

export default App;
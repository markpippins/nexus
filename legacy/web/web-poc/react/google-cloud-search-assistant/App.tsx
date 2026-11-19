
import React, { useState, useCallback } from 'react';
import { performGoogleSearch } from './services/geminiService';
import { SearchResult } from './types';
import SearchForm from './components/SearchForm';
import ResultsDisplay from './components/ResultsDisplay';
import Spinner from './components/Spinner';

const App: React.FC = () => {
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = useCallback(async (query: string) => {
    setIsLoading(true);
    setResult(null);
    setError(null);
    try {
      const searchResult = await performGoogleSearch(query);
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
  }, []);

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900 text-brand-text-primary dark:text-gray-200 font-sans p-4 sm:p-6 lg:p-8">
      <main className="container mx-auto">
        <header className="text-center my-8 md:my-12">
          <h1 className="text-4xl sm:text-5xl md:text-6xl font-extrabold tracking-tight text-transparent bg-clip-text bg-gradient-to-r from-blue-500 to-teal-400">
            Google Cloud Search Assistant
          </h1>
          <p className="mt-4 text-lg text-brand-text-secondary dark:text-gray-400 max-w-2xl mx-auto">
            Leverage Gemini's Google Search grounding to get up-to-date answers and export results for local processing.
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
            <p className="text-sm text-gray-500 dark:text-gray-400">Powered by Google Gemini</p>
        </footer>
      </main>
    </div>
  );
};

export default App;

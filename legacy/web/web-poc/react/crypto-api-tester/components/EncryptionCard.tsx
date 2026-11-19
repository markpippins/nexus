
import React, { useState } from 'react';
import CopyIcon from './icons/CopyIcon';
import CheckIcon from './icons/CheckIcon';

interface EncryptionCardProps {
  title: string;
  description: string;
  inputLabel: string;
  buttonText: string;
  isCiphertext?: boolean;
  onProcess: (text: string, key: string) => Promise<string>;
}

const EncryptionCard: React.FC<EncryptionCardProps> = ({
  title,
  description,
  inputLabel,
  buttonText,
  isCiphertext = false,
  onProcess,
}) => {
  const [inputText, setInputText] = useState('');
  const [secretKey, setSecretKey] =useState('');
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [hasCopied, setHasCopied] = useState(false);

  const handleProcessClick = async () => {
    if (!inputText || !secretKey) {
      setError('Both input text and secret key are required.');
      return;
    }
    setIsLoading(true);
    setError('');
    setResult('');
    setHasCopied(false);

    try {
      const output = await onProcess(inputText, secretKey);
      setResult(output);
    } catch (e) {
      if (e instanceof Error) {
        setError(e.message);
      } else {
        setError('An unknown error occurred.');
      }
    } finally {
      setIsLoading(false);
    }
  };

  const handleCopy = () => {
    if (result) {
      navigator.clipboard.writeText(result);
      setHasCopied(true);
      setTimeout(() => setHasCopied(false), 2000);
    }
  };

  return (
    <div className="bg-slate-800/50 rounded-lg shadow-lg p-6 flex flex-col border border-slate-700 hover:border-cyan-500/50 transition-colors duration-300">
      <h2 className="text-2xl font-bold text-cyan-400 mb-2">{title}</h2>
      <p className="text-slate-400 mb-6 text-sm flex-grow">{description}</p>
      
      <div className="space-y-4">
        <div>
          <label htmlFor={`input-${title}`} className="block text-sm font-medium text-slate-300 mb-1">
            {inputLabel}
          </label>
          <textarea
            id={`input-${title}`}
            rows={3}
            className={`w-full bg-slate-900/70 border border-slate-600 rounded-md p-2 text-slate-200 focus:ring-2 focus:ring-cyan-500 focus:border-cyan-500 transition-shadow ${isCiphertext ? 'font-mono text-xs' : ''}`}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder={isCiphertext ? 'Enter ciphertext...' : 'Enter plaintext...'}
          />
        </div>
        <div>
          <label htmlFor={`key-${title}`} className="block text-sm font-medium text-slate-300 mb-1">
            Secret Key
          </label>
          <input
            id={`key-${title}`}
            type="password"
            className="w-full bg-slate-900/70 border border-slate-600 rounded-md p-2 text-slate-200 focus:ring-2 focus:ring-cyan-500 focus:border-cyan-500 transition-shadow"
            value={secretKey}
            onChange={(e) => setSecretKey(e.target.value)}
            placeholder="Enter your secret key..."
          />
        </div>
      </div>
      
      <button
        onClick={handleProcessClick}
        disabled={isLoading}
        className="mt-6 w-full bg-cyan-600 hover:bg-cyan-500 disabled:bg-slate-600 disabled:cursor-not-allowed text-white font-bold py-2 px-4 rounded-md transition-all duration-300 flex items-center justify-center"
      >
        {isLoading ? (
            <>
                <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                Processing...
            </>
        ) : (
          buttonText
        )}
      </button>

      {error && (
        <div className="mt-4 bg-red-900/50 border border-red-700 text-red-300 px-4 py-3 rounded-md text-sm">
          <p><span className="font-bold">Error:</span> {error}</p>
        </div>
      )}

      {result && (
        <div className="mt-6">
          <h3 className="text-lg font-semibold text-slate-300 mb-2">Result:</h3>
          <div className="relative bg-slate-900 rounded-md p-4 border border-slate-700">
            <pre className="text-sm text-green-300 whitespace-pre-wrap break-all font-mono">
              <code>{result}</code>
            </pre>
            <button 
              onClick={handleCopy}
              className="absolute top-2 right-2 p-1.5 bg-slate-700 hover:bg-slate-600 rounded-md text-slate-300 transition-colors"
              aria-label="Copy result to clipboard"
            >
              {hasCopied ? <CheckIcon className="w-5 h-5 text-green-400" /> : <CopyIcon className="w-5 h-5" />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default EncryptionCard;

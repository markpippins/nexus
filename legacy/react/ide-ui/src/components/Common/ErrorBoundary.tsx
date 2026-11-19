import React from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface ErrorBoundaryProps {
  children: React.ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('[Nebula ErrorBoundary] Render crash caught:', error, errorInfo);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-slate-100 dark:bg-slate-950 text-slate-900 dark:text-slate-100 font-sans">
          <div className="max-w-md w-full mx-4 p-6 bg-white dark:bg-slate-900 border border-slate-300 dark:border-slate-800 rounded-lg shadow-lg text-center">
            <AlertTriangle className="w-12 h-12 text-amber-500 mx-auto mb-4" />
            <h1 className="text-lg font-bold font-mono text-slate-800 dark:text-slate-200 mb-2">
              Rendering Error
            </h1>
            <p className="text-sm text-slate-600 dark:text-slate-400 mb-2">
              A component crashed during rendering. This is likely a transient data issue.
            </p>
            {this.state.error && (
              <pre className="text-sm text-left text-red-600 dark:text-red-400 bg-slate-100 dark:bg-slate-950 p-2 rounded mb-4 max-h-32 overflow-auto font-mono">
                {this.state.error.message}
              </pre>
            )}
            <button
              onClick={() => window.location.reload()}
              className="inline-flex items-center gap-2 px-4 py-2 bg-sky-600 hover:bg-sky-700 text-white rounded text-sm font-medium transition-colors"
            >
              <RefreshCw className="w-4 h-4" />
              Reload Application
            </button>
            <p className="text-sm text-slate-500 dark:text-slate-500 mt-3">
              If this persists, check the browser console (F12) for details.
            </p>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

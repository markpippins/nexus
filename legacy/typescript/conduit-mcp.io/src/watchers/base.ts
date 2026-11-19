/** Base class for all sub-watchers. */
export abstract class BaseWatcher {
  constructor(
    protected baseDir: string,
    protected emit: (event: any) => void,
  ) {}

  /** Initialize the watcher (scan + start watching). */
  abstract initialize(): Promise<void>;

  /** Clean up resources (timers, file watchers). */
  abstract destroy(): void;
}

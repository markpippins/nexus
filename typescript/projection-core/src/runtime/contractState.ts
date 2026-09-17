export interface ContractStateStore<TContract = unknown> {
  get(): TContract;
  set(patch: Partial<TContract>): void;
  replace(next: TContract): void;
  subscribe(fn: (state: TContract) => void): () => void;
  reset(): void;
}

export class InMemoryContractStateStore<TContract> implements ContractStateStore<TContract> {
  private state: TContract;
  private listeners: Set<(state: TContract) => void> = new Set();

  constructor(initialState: TContract) {
    this.state = { ...initialState };
  }

  get(): TContract {
    return { ...this.state };
  }

  set(patch: Partial<TContract>): void {
    this.state = { ...this.state, ...patch };
    this.notify();
  }

  replace(next: TContract): void {
    this.state = { ...next };
    this.notify();
  }

  subscribe(fn: (state: TContract) => void): () => void {
    this.listeners.add(fn);
    fn(this.get());
    return () => {
      this.listeners.delete(fn);
    };
  }

  reset(): void {
    this.state = {} as TContract;
    this.notify();
  }

  private notify(): void {
    const currentState = this.get();
    for (const listener of this.listeners) {
      try {
        listener(currentState);
      } catch (error) {
        console.error("ContractStateStore listener error:", error);
      }
    }
  }
}

export interface ContractStateRegistry {
  getStore<TContract>(widgetId: string): ContractStateStore<TContract>;
  createStore<TContract>(widgetId: string, initialState: TContract): void;
  hasStore(widgetId: string): boolean;
  deleteStore(widgetId: string): void;
  getAllStores(): Map<string, ContractStateStore>;
}

export class InMemoryContractStateRegistry implements ContractStateRegistry {
  private stores: Map<string, ContractStateStore> = new Map();

  getStore<TContract>(widgetId: string): ContractStateStore<TContract> {
    const store = this.stores.get(widgetId);
    if (!store) {
      throw new Error(`No contract state store found for widget: ${widgetId}`);
    }
    return store as ContractStateStore<TContract>;
  }

  createStore<TContract>(widgetId: string, initialState: TContract): void {
    if (this.stores.has(widgetId)) {
      console.warn(`Store already exists for widget: ${widgetId}, replacing`);
    }
    this.stores.set(widgetId, new InMemoryContractStateStore(initialState));
  }

  hasStore(widgetId: string): boolean {
    return this.stores.has(widgetId);
  }

  deleteStore(widgetId: string): void {
    this.stores.delete(widgetId);
  }

  getAllStores(): Map<string, ContractStateStore> {
    return new Map(this.stores);
  }
}

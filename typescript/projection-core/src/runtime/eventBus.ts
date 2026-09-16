import { EventBus, RuntimeEvent, EventHandler } from "./types";

export class SimpleEventBus implements EventBus {
  private handlers: Map<string, Set<EventHandler>> = new Map();
  private widgetHandlers: Map<string, Set<EventHandler>> = new Map();

  subscribe(widgetId: string, handler: EventHandler): void {
    if (!this.widgetHandlers.has(widgetId)) {
      this.widgetHandlers.set(widgetId, new Set());
    }
    this.widgetHandlers.get(widgetId)!.add(handler);
  }

  unsubscribe(widgetId: string, handler: EventHandler): void {
    const handlers = this.widgetHandlers.get(widgetId);
    if (handlers) {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.widgetHandlers.delete(widgetId);
      }
    }
  }

  emit(event: RuntimeEvent): void {
    const widgetHandlers = this.widgetHandlers.get(event.source);
    if (widgetHandlers) {
      for (const handler of widgetHandlers) {
        handler(event);
      }
    }

    const globalHandlers = this.handlers.get(event.type);
    if (globalHandlers) {
      for (const handler of globalHandlers) {
        handler(event);
      }
    }

    const wildcardHandlers = this.handlers.get("*");
    if (wildcardHandlers) {
      for (const handler of wildcardHandlers) {
        handler(event);
      }
    }
  }

  on(event: string, handler: EventHandler): void {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, new Set());
    }
    this.handlers.get(event)!.add(handler);
  }

  off(event: string, handler: EventHandler): void {
    const handlers = this.handlers.get(event);
    if (handlers) {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.handlers.delete(event);
      }
    }
  }
}


import { Injectable, signal, computed } from '@angular/core';
import { ComponentConfig, INITIAL_REGISTRY, NodeType } from '../config/component-config';

@Injectable({
  providedIn: 'root'
})
export class ComponentRegistryService {
  // Master list of all components
  private registry = signal<ComponentConfig[]>([...INITIAL_REGISTRY]);

  // Derived Accessors
  public allComponents = this.registry.asReadonly();
  
  public availableTypes = computed(() => this.registry().map(c => c.type));

  constructor() {
    // Ideally load from LocalStorage here
  }

  getConfig(type: NodeType): ComponentConfig {
    const config = this.registry().find(c => c.type === type);
    if (!config) {
        // Fallback to internal if missing (e.g. deleted custom type)
        return this.registry().find(c => c.type === 'internal') || INITIAL_REGISTRY[0];
    }
    return config;
  }

  getConfigById(id: string): ComponentConfig | undefined {
    return this.registry().find(c => c.id === id);
  }

  addComponent(config: ComponentConfig) {
    this.registry.update(current => [...current, config]);
  }

  updateComponent(id: string, updates: Partial<ComponentConfig>) {
    this.registry.update(current => 
      current.map(c => c.id === id ? { ...c, ...updates } : c)
    );
  }

  deleteComponent(id: string) {
    const config = this.getConfigById(id);
    if (config?.isSystem) return; // Protect system components
    this.registry.update(current => current.filter(c => c.id !== id));
  }

  // Generates a new component config based on a parent (subclassing)
  createDerivedComponent(parentId: string, newLabel: string): ComponentConfig {
    const parent = this.getConfigById(parentId);
    if (!parent) throw new Error('Parent not found');

    const newSlug = newLabel.toLowerCase().replace(/[^a-z0-9]/g, '-');
    
    return {
      ...parent, // Copy all parent props
      id: crypto.randomUUID(),
      parentId: parent.id,
      isSystem: false,
      type: `custom-${newSlug}-${Date.now().toString().slice(-4)}`,
      label: newLabel,
      defaultNamePrefix: newLabel,
      description: `Derived from ${parent.label}`,
      category: 'Custom'
    };
  }
}

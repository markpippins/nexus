
import { Component, ElementRef, ViewChild, AfterViewInit, OnDestroy, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import * as THREE from 'three';
import { ComponentRegistryService } from '../services/component-registry.service';
import { ComponentConfig } from '../config/component-config';

@Component({
  selector: 'app-component-creator',
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `
    <div class="flex h-full w-full bg-slate-900 text-slate-200">
      
      <!-- LEFT PANE: Component Browser -->
      <div class="w-64 border-r border-slate-700 flex flex-col bg-slate-950/50">
        <div class="p-4 border-b border-slate-700">
          <h2 class="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2">Component Library</h2>
          <input type="text" placeholder="Filter..." class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs focus:border-cyan-500 outline-none">
        </div>
        
        <div class="flex-1 overflow-y-auto p-2 space-y-1">
          <div class="text-[10px] text-slate-500 px-2 py-1 uppercase font-bold">System</div>
          @for (comp of systemComponents(); track comp.id) {
            <button (click)="selectComponent(comp)" 
                    class="w-full text-left px-3 py-2 rounded border border-transparent hover:bg-slate-800/50 flex items-center gap-2 group cursor-pointer transition-colors">
              <div [class]="comp.colorClass + ' ' + comp.iconClass" class="w-3 h-3 shadow-sm"></div>
              <span class="text-sm truncate">{{ comp.label }}</span>
            </button>
          }

          <div class="text-[10px] text-slate-500 px-2 py-1 uppercase font-bold mt-4">Custom</div>
          @for (comp of customComponents(); track comp.id) {
            <button (click)="selectComponent(comp)" 
                    [class.bg-slate-800]="selectedId() === comp.id"
                    [class.border-cyan-500]="selectedId() === comp.id"
                    class="w-full text-left px-3 py-2 rounded border border-transparent hover:bg-slate-800/50 flex items-center gap-2 group cursor-pointer transition-colors">
              <div [class]="comp.colorClass + ' ' + comp.iconClass" class="w-3 h-3 shadow-sm"></div>
              <span class="text-sm truncate">{{ comp.label }}</span>
            </button>
          }
           @if (customComponents().length === 0) {
             <div class="text-xs text-slate-600 px-3 py-2 italic">No custom components yet.</div>
           }
        </div>

        <div class="p-4 border-t border-slate-700">
          <button (click)="startNew()" class="w-full bg-cyan-700 hover:bg-cyan-600 text-white py-2 rounded text-xs font-bold transition">
            + Create New
          </button>
        </div>
      </div>

      <!-- CENTER PANE: Editor -->
      <div class="flex-1 flex flex-col border-r border-slate-700 overflow-hidden relative">
        @if (activeConfig()) {
          <div class="p-4 border-b border-slate-700 bg-slate-900/80 flex justify-between items-center">
            <div>
              <h2 class="text-sm font-bold text-white">{{ isEditingExisting ? 'Edit Component' : 'New Component' }}</h2>
              @if (activeConfig()!.parentId) {
                <p class="text-xs text-slate-400">Extends: {{ getParentName(activeConfig()!.parentId!) }}</p>
              }
            </div>
            
            <div class="flex gap-2">
              @if (isEditingExisting && !activeConfig()!.isSystem) {
                <button (click)="deleteCurrent()" class="text-red-400 text-xs px-3 hover:underline">Delete</button>
              }
              <button (click)="cancel()" class="text-slate-400 hover:text-white text-xs px-3 hover:underline">Cancel</button>
              <button (click)="save()" class="bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-1 rounded text-xs font-bold">Save</button>
            </div>
          </div>

          <div class="flex-1 overflow-y-auto p-6 space-y-6">
            
            <!-- Identity Section -->
            <section class="space-y-3">
              <h3 class="text-xs font-bold text-cyan-500 uppercase tracking-wider border-b border-slate-800 pb-1">Identity</h3>
              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="block text-xs text-slate-400 mb-1">Label Name</label>
                  <input [ngModel]="activeConfig()!.label" (ngModelChange)="updateField('label', $event)" 
                         class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm outline-none focus:border-cyan-500">
                </div>
                 <div>
                  <label class="block text-xs text-slate-400 mb-1">Category</label>
                  <input [ngModel]="activeConfig()!.category" (ngModelChange)="updateField('category', $event)"
                         class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm outline-none focus:border-cyan-500">
                </div>
              </div>
              <div>
                <label class="block text-xs text-slate-400 mb-1">Description</label>
                <textarea [ngModel]="activeConfig()!.description" (ngModelChange)="updateField('description', $event)"
                          rows="2" class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm outline-none focus:border-cyan-500"></textarea>
              </div>
            </section>

            <!-- Visuals Section -->
            <section class="space-y-3">
              <h3 class="text-xs font-bold text-cyan-500 uppercase tracking-wider border-b border-slate-800 pb-1">Visuals</h3>
              <div class="grid grid-cols-2 gap-4">
                <div>
                  <label class="block text-xs text-slate-400 mb-1">Geometry / Shape</label>
                  <select [ngModel]="activeConfig()!.geometry" (ngModelChange)="updateField('geometry', $event)"
                          class="w-full bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm outline-none focus:border-cyan-500">
                    <option value="sphere">Sphere</option>
                    <option value="box">Box</option>
                    <option value="cylinder">Cylinder</option>
                    <option value="tall-cylinder">Tall Server</option>
                    <option value="octahedron">Octahedron</option>
                    <option value="icosahedron">Icosahedron</option>
                    <option value="torus">Torus</option>
                  </select>
                </div>
                <div>
                  <label class="block text-xs text-slate-400 mb-1">Color (Hex)</label>
                  <div class="flex gap-2">
                    <input type="color" [value]="colorHexStr" (input)="onColorChange($event)" class="h-8 w-12 bg-transparent cursor-pointer rounded border border-slate-700">
                     <span class="text-xs font-mono self-center text-slate-400">{{ colorHexStr }}</span>
                  </div>
                </div>
                <div>
                  <label class="block text-xs text-slate-400 mb-1">Scale</label>
                  <input type="range" min="0.5" max="5" step="0.1" [ngModel]="activeConfig()!.scale" (ngModelChange)="updateField('scale', $event)" class="w-full accent-cyan-500">
                  <div class="text-right text-xs text-slate-500">{{ activeConfig()!.scale }}x</div>
                </div>
              </div>
            </section>

            <!-- Rules Section -->
            <section class="space-y-3">
              <h3 class="text-xs font-bold text-cyan-500 uppercase tracking-wider border-b border-slate-800 pb-1">Behavior & Rules</h3>
              
              <div>
                <label class="block text-xs text-slate-400 mb-2">Allowed Outbound Connections</label>
                <div class="bg-slate-950 border border-slate-700 rounded p-2 max-h-40 overflow-y-auto grid grid-cols-1 gap-1">
                   <label class="flex items-center gap-2 text-sm hover:bg-slate-900 p-1 rounded cursor-pointer">
                     <input type="checkbox" [checked]="isAllowedAll()" (change)="toggleAllowed('all')">
                     <span class="font-bold text-cyan-200">Allow All</span>
                   </label>
                   
                   @if (!isAllowedAll()) {
                     @for (type of allTypes(); track type) {
                       <label class="flex items-center gap-2 text-sm hover:bg-slate-900 p-1 rounded cursor-pointer">
                         <input type="checkbox" [checked]="isConnectionAllowed(type)" (change)="toggleAllowed(type)">
                         <span class="text-slate-300">{{ getLabelForType(type) }}</span>
                       </label>
                     }
                   }
                </div>
              </div>
            </section>
          </div>
        } @else {
          <div class="flex-1 flex flex-col items-center justify-center text-slate-500">
            <div class="text-4xl mb-4">🛠️</div>
            <p>Select a component to edit or create a new one.</p>
          </div>
        }
      </div>

      <!-- RIGHT PANE: Preview -->
      <div class="w-72 bg-slate-950 flex flex-col">
         <div class="p-2 border-b border-slate-800 text-center">
           <span class="text-[10px] font-bold text-slate-500 uppercase">Live Preview</span>
         </div>
         <div class="flex-1 relative" #previewContainer></div>
         <div class="p-4 border-t border-slate-800 text-xs text-slate-500 space-y-2">
           @if (activeConfig(); as form) {
             <p><strong>ID:</strong> {{ form.id }}</p>
             <p><strong>Type Slug:</strong> {{ form.type }}</p>
             <div class="mt-4 p-2 bg-slate-900 rounded border border-slate-800">
               <div class="flex items-center gap-2">
                  <div [class]="form.colorClass" class="w-3 h-3 rounded-full"></div>
                  <span>Icon Style Preview</span>
               </div>
             </div>
           }
         </div>
      </div>

    </div>
  `
})
export class ComponentCreatorComponent implements AfterViewInit, OnDestroy {
  @ViewChild('previewContainer') previewContainer!: ElementRef<HTMLDivElement>;

  // Data Signals
  systemComponents = computed(() => this.registry.allComponents().filter(c => c.isSystem));
  customComponents = computed(() => this.registry.allComponents().filter(c => !c.isSystem));
  allTypes = this.registry.availableTypes;
  
  selectedId = signal<string | null>(null);
  
  // Active Config (The form state)
  activeConfig = signal<ComponentConfig | null>(null);
  isEditingExisting = false;

  // Previewer State
  private scene!: THREE.Scene;
  private camera!: THREE.PerspectiveCamera;
  private renderer!: THREE.WebGLRenderer;
  private mesh!: THREE.Mesh;
  private frameId: number | null = null;

  constructor(private registry: ComponentRegistryService) {}

  ngAfterViewInit() {
    this.initPreview();
  }

  ngOnDestroy() {
    if (this.frameId) cancelAnimationFrame(this.frameId);
    if (this.renderer) this.renderer.dispose();
  }

  // --- Actions ---

  selectComponent(comp: ComponentConfig) {
    if (comp.isSystem) {
        // System components are read-only, suggest inheritance
        if (confirm(`System components are read-only. Create a new component based on '${comp.label}'?`)) {
            this.createFrom(comp);
        }
    } else {
        // Deep copy for form
        const copy = JSON.parse(JSON.stringify(comp));
        this.selectedId.set(comp.id);
        this.activeConfig.set(copy);
        this.isEditingExisting = true;
        this.updatePreview();
    }
  }

  startNew() {
    const base = this.systemComponents()[0]; // Default to first available
    this.createFrom(base);
  }

  createFrom(parent: ComponentConfig) {
    const newConfig = this.registry.createDerivedComponent(parent.id, `New ${parent.label}`);
    this.activeConfig.set(newConfig);
    this.selectedId.set(null);
    this.isEditingExisting = false;
    this.updatePreview();
  }

  cancel() {
    this.activeConfig.set(null);
    this.selectedId.set(null);
  }

  save() {
    const form = this.activeConfig();
    if (!form) return;
    
    if (this.isEditingExisting) {
        this.registry.updateComponent(form.id, form);
    } else {
        this.registry.addComponent(form);
        this.selectedId.set(form.id); 
        this.isEditingExisting = true;
    }
    alert('Component Saved!');
  }

  deleteCurrent() {
      const form = this.activeConfig();
      if (form && !form.isSystem) {
          if(confirm('Delete this component definition? Existing nodes on the canvas may break.')) {
              this.registry.deleteComponent(form.id);
              this.activeConfig.set(null);
          }
      }
  }

  // --- Form Updates ---

  updateField(field: keyof ComponentConfig, value: any) {
    const current = this.activeConfig();
    if (current) {
        // Create new object reference to trigger signal if needed, but here we just mutate the copy
        // and manually trigger preview. Since activeConfig is a signal of a mutable object,
        // we can just mutate it. But for safety with checking activeConfig(), we keep it simple.
        (current as any)[field] = value;
        this.updatePreview();
    }
  }

  getParentName(id: string): string {
    return this.registry.getConfigById(id)?.label || 'Unknown';
  }

  get colorHexStr(): string {
      const form = this.activeConfig();
      if (!form) return '#ffffff';
      return '#' + new THREE.Color(form.defaultColor).getHexString();
  }

  onColorChange(event: Event) {
      const val = (event.target as HTMLInputElement).value;
      const form = this.activeConfig();
      if (form) {
          form.defaultColor = parseInt(val.replace('#', ''), 16);
          this.updatePreview();
      }
  }

  getLabelForType(type: string): string {
      return this.registry.getConfig(type).label;
  }

  // --- Connection Logic ---

  isAllowedAll(): boolean {
      const form = this.activeConfig();
      return form ? form.allowedConnections === 'all' : false;
  }

  isConnectionAllowed(type: string): boolean {
      const form = this.activeConfig();
      if (!form) return false;
      if (form.allowedConnections === 'all') return true;
      return form.allowedConnections.includes(type);
  }

  toggleAllowed(type: string | 'all') {
      const form = this.activeConfig();
      if (!form) return;

      if (type === 'all') {
          if (form.allowedConnections === 'all') {
              form.allowedConnections = [];
          } else {
              form.allowedConnections = 'all';
          }
      } else {
          let current: string[] = Array.isArray(form.allowedConnections) 
             ? form.allowedConnections 
             : [];
          
          if (current.includes(type)) {
              current = current.filter(t => t !== type);
          } else {
              current.push(type);
          }
          form.allowedConnections = current;
      }
  }


  // --- 3D Preview Logic ---

  initPreview() {
    if (!this.previewContainer) return;
    const width = this.previewContainer.nativeElement.clientWidth;
    const height = 300; 

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x020617);

    this.camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 100);
    this.camera.position.z = 4;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setSize(width, height);
    this.previewContainer.nativeElement.appendChild(this.renderer.domElement);

    const light = new THREE.DirectionalLight(0xffffff, 1);
    light.position.set(2, 2, 5);
    this.scene.add(light);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.5));

    this.animate();
  }

  updatePreview() {
    const form = this.activeConfig();
    if (!form || !this.scene) return;

    if (this.mesh) {
        this.scene.remove(this.mesh);
        this.mesh.geometry.dispose();
    }

    let geo: THREE.BufferGeometry;
    switch (form.geometry) {
      case 'sphere': geo = new THREE.SphereGeometry(0.7, 32, 16); break;
      case 'torus': geo = new THREE.TorusGeometry(0.6, 0.2, 16, 100); break;
      case 'octahedron': geo = new THREE.OctahedronGeometry(1); break;
      case 'cylinder': geo = new THREE.CylinderGeometry(0.5, 0.5, 1, 32); break;
      case 'icosahedron': geo = new THREE.IcosahedronGeometry(1); break;
      case 'box': geo = new THREE.BoxGeometry(1, 1, 1); break;
      case 'tall-cylinder': geo = new THREE.CylinderGeometry(0.5, 0.5, 2, 32); break;
      default: geo = new THREE.BoxGeometry(1, 1, 1);
    }

    const mat = new THREE.MeshPhongMaterial({
        color: form.defaultColor,
        shininess: 100,
        flatShading: true
    });

    this.mesh = new THREE.Mesh(geo, mat);
    const scale = form.scale; 
    const displayScale = Math.min(Math.max(scale, 0.5), 2.5); 
    this.mesh.scale.setScalar(displayScale);
    
    this.scene.add(this.mesh);
  }

  animate() {
      this.frameId = requestAnimationFrame(() => this.animate());
      if (this.mesh) {
          this.mesh.rotation.x += 0.01;
          this.mesh.rotation.y += 0.01;
      }
      if (this.renderer && this.scene && this.camera) {
        this.renderer.render(this.scene, this.camera);
      }
  }
}

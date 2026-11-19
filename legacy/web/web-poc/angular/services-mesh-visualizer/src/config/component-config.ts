
export type NodeType = string; // Relaxed from union type to string to support dynamic types

export interface ComponentConfig {
  id: string;            // Unique Internal ID
  type: NodeType;        // Slug used in code/save files (e.g., 'client', 'custom-1')
  label: string;         // Display label
  defaultNamePrefix: string; 
  description?: string;
  
  // Inheritance
  parentId?: string;     // ID of the component this extends
  isSystem?: boolean;    // If true, cannot be deleted/edited
  category?: string;     // For grouping

  // 3D Visual Properties
  geometry: 'sphere' | 'torus' | 'octahedron' | 'cylinder' | 'icosahedron' | 'box' | 'tall-cylinder';
  defaultColor: number; 
  scale: number;

  // UI Palette Properties
  iconClass: string; 
  colorClass: string; 
  
  // Logic Rules
  allowedConnections: NodeType[] | 'all'; 
}

// Initial System Components
export const INITIAL_REGISTRY: ComponentConfig[] = [
  // --- API Services ---
  { 
    id: 'sys-rest',
    type: 'rest-api', 
    label: 'REST API Service', 
    defaultNamePrefix: 'API',
    geometry: 'tall-cylinder', 
    defaultColor: 0x0ea5e9, // Sky 500
    scale: 2,
    iconClass: 'rounded-sm',
    colorClass: 'bg-sky-500',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Services'
  },
  { 
    id: 'sys-graphql',
    type: 'graphql-api', 
    label: 'GraphQL API', 
    defaultNamePrefix: 'GQL',
    geometry: 'icosahedron', 
    defaultColor: 0xe11d48, // Rose 600
    scale: 2,
    iconClass: 'rounded-full',
    colorClass: 'bg-rose-600',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Services'
  },
  { 
    id: 'sys-grpc',
    type: 'grpc-service', 
    label: 'gRPC Service', 
    defaultNamePrefix: 'gRPC',
    geometry: 'box', 
    defaultColor: 0x0d9488, // Teal 600
    scale: 1.5,
    iconClass: 'rounded-sm',
    colorClass: 'bg-teal-600',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Services'
  },
  { 
    id: 'sys-job',
    type: 'background-job', 
    label: 'Background Job', 
    defaultNamePrefix: 'Worker',
    geometry: 'octahedron', 
    defaultColor: 0xeab308, // Yellow 500
    scale: 1.3,
    iconClass: 'rotate-45',
    colorClass: 'bg-yellow-500',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Services'
  },

  // --- Infrastructure ---
  { 
    id: 'sys-gateway',
    type: 'gateway', 
    label: 'API Gateway', 
    defaultNamePrefix: 'Gateway',
    geometry: 'octahedron', 
    defaultColor: 0xa855f7, // Purple 500
    scale: 2.5,
    iconClass: 'rotate-45',
    colorClass: 'bg-purple-500',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Infrastructure'
  },
  { 
    id: 'sys-proxy',
    type: 'proxy', 
    label: 'Reverse Proxy', 
    defaultNamePrefix: 'Proxy',
    geometry: 'torus', 
    defaultColor: 0x10b981, // Emerald 500
    scale: 2,
    iconClass: 'rounded-full ring-4 ring-emerald-500',
    colorClass: 'bg-transparent',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Infrastructure'
  },
  { 
    id: 'sys-queue',
    type: 'message-queue', 
    label: 'Message Queue', 
    defaultNamePrefix: 'Queue',
    geometry: 'torus', 
    defaultColor: 0xf97316, // Orange 500
    scale: 2,
    iconClass: 'rounded-full ring-2',
    colorClass: 'bg-orange-500',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Infrastructure'
  },
  { 
    id: 'sys-jms',
    type: 'jms-queue', 
    label: 'JMS Queue', 
    defaultNamePrefix: 'JMS',
    geometry: 'torus', 
    defaultColor: 0xf59e0b, // Amber 500
    scale: 2,
    iconClass: 'rounded-full ring-2 border-dashed',
    colorClass: 'bg-amber-500',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Infrastructure'
  },

  // --- Storage ---
  { 
    id: 'sys-db',
    type: 'database', 
    label: 'Database', 
    defaultNamePrefix: 'DB',
    geometry: 'cylinder', 
    defaultColor: 0x334155, // Slate 700
    scale: 2,
    iconClass: 'rounded-b-md rounded-t-md',
    colorClass: 'bg-slate-700 h-5',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Storage'
  },
  { 
    id: 'sys-cache',
    type: 'cache', 
    label: 'Cache Service', 
    defaultNamePrefix: 'Cache',
    geometry: 'box', 
    defaultColor: 0xdc2626, // Red 600
    scale: 1.2,
    iconClass: 'rounded-sm',
    colorClass: 'bg-red-600',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Storage'
  },

  // --- Clients ---
  { 
    id: 'sys-webapp',
    type: 'web-app', 
    label: 'Web Application', 
    defaultNamePrefix: 'Web',
    geometry: 'sphere', 
    defaultColor: 0x2563eb, // Blue 600
    scale: 1.5,
    iconClass: 'rounded-full',
    colorClass: 'bg-blue-600',
    allowedConnections: 'all',
    isSystem: true,
    category: 'Client'
  },
];

// Deprecated: Use ComponentRegistryService instead
export const COMPONENT_REGISTRY = INITIAL_REGISTRY;
export const getComponentConfig = (type: NodeType) => INITIAL_REGISTRY.find(c => c.type === type)!;

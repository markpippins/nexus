/**
 * Nexus Lookup Service Models
 * 
 * Simple lookup entities for the service registry.
 * These are reference data entities with standard CRUD patterns.
 */

/**
 * Base model for all lookup entities.
 * Provides common fields and consistency across lookups.
 */
namespace com.aibizarchitect.nexus.v1.core.lookups {

model LookupBase {
  /** Unique identifier */
  @visibility(Lifecycle.Read)
  id: int64;

  /** Display name - unique within the lookup type */
  @minLength(1)
  @maxLength(255)
  name: string;

  /** Whether this lookup value is active/available */
  activeFlag?: boolean = true;
}

/**
 * Extended lookup with description support.
 */
model DescribedLookup extends LookupBase {
  /** Detailed description of the lookup value */
  @maxLength(1000)
  description?: string;
}

/**
 * Extended lookup with URL support.
 */
model LinkedLookup extends DescribedLookup {
  /** External URL for reference (documentation, vendor site, etc.) */
  @format("url")
  url?: string;
}

/**
 * Framework vendor lookup.
 * Vendors provide frameworks (Spring, Quarkus, etc.)
 */
model FrameworkVendor extends LinkedLookup {
  /** Vendor name (e.g., "VMware", "Red Hat") */
  name: string;
}
}
/** Basic domain entities for the service registry (migrations from Spring v1). */
namespace com.aibizarchitect.nexus.v1.serviceregistry {
  
  model EnvironmentType {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model Host {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model Service {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model Deployment {
    id: int64;
    service?: string;
    environment?: string;
    host?: string;
    version?: string;
    deployedAt?: utcDateTime;
    status?: string;
    port?: int32;
    contextPath?: string;
    healthCheckUrl?: string;
    healthStatus?: string;
    lastHealthCheck?: utcDateTime;
    processId?: string;
    containerName?: string;
    deploymentPath?: string;
    startedAt?: utcDateTime;
    stoppedAt?: utcDateTime;
    activeFlag?: boolean = true;
    createdAt?: utcDateTime;
    updatedAt?: utcDateTime;
  }
}

/**
 * Framework category lookup.
 * Categorizes frameworks by their general type.
 */
// model FrameworkCategory extends LookupBase {
//   /** Category name (e.g., "Web Framework", "Micro Framework") */
//   name: string;
  
//   // Note: description field removed but kept in model for backward compat
//   // Actual value will be null
//   @visibility(Lifecycle.Read)
//   description?: string;
// }

/**
 * Framework language lookup.
 * Programming languages supported by the platform.
 */
namespace com.aibizarchitect.nexus.v1.core.lookups {
model FrameworkLanguage extends LinkedLookup {
  /** Language name (e.g., "Java", "TypeScript") */
  name: string;

  /** Current stable version of the language */
  currentVersion?: string;

  /** Long-term support version of the language */
  ltsVersion?: string;
}
}
// Framework entity models pulled from Spring lookups will be added here via canonical core adapters
namespace com.aibizarchitect.nexus.v1.serviceregistry {

  model FrameworkVendor {
    id: int64;
    name: string;
    description?: string;
    url?: string;
    activeFlag?: boolean = true;
  }
  model FrameworkLanguage {
    id: int64;
    name: string;
    currentVersion?: string;
    ltsVersion?: string;
  }
  model Framework {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model FrameworkCategory {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  // model EnvironmentType {
  //   id: int64;
  //   name: string;
  //   activeFlag?: boolean = true;
  // }
  // model Host {
  //   id: int64;
  //   name: string;
  //   activeFlag?: boolean = true;
  // }
  // model Deployment {
  //   id: int64;
  // }
  
  model Library {
    id: int64;
    name: string;
    version?: string;
  }
  model LibraryCategory {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model OperatingSystem {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model ServerType {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  // model Service {
  //   id: int64;
  //   name: string;
  //   activeFlag?: boolean = true;
  // }
  model ServiceBackend {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model ServiceConfiguration {
    id: int64;
    key: string;
    value?: string;
  }
  model ServiceDependency {
    id: int64;
    name: string;
  }
  model ServiceLibrary {
    id: int64;
    name: string;
    version?: string;
  }
  model ServiceType {
    id: int64;
    name: string;
  }
  model VisualComponent {
    id: int64;
    name: string;
  }
  model FrameworkVendorCreate {
    id?: int64;
    name: string;
    description?: string;
    url?: string;
    activeFlag?: boolean = true;
  }
  model FrameworkVendorUpdate {
    id: int64;
    name?: string;
    description?: string;
    url?: string;
    activeFlag?: boolean;
  }
  model FrameworkCategoryCreate {
    id?: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model FrameworkCategoryUpdate {
    id: int64;
    name?: string;
    activeFlag?: boolean;
  }
}
  model Framework {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model FrameworkCategory {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model Host {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model EnvironmentType {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  // model Host {
  //   id: int64;
  //   name: string;
  //   activeFlag?: boolean = true;
  // }
  model Deployment {
    id: int64;
    service?: string;
    environment?: string;
    host?: string;
    version?: string;
    deployedAt?: utcDateTime;
    status?: string;
    port?: int32;
    contextPath?: string;
    healthCheckUrl?: string;
    healthStatus?: string;
    lastHealthCheck?: utcDateTime;
    startedAt?: utcDateTime;
    stoppedAt?: utcDateTime;
    activeFlag?: boolean = true;
    createdAt?: utcDateTime;
    updatedAt?: utcDateTime;
  }
  model Library {
    id: int64;
    name: string;
    version?: string;
    activeFlag?: boolean = true;
  }
  model LibraryCategory {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model OperatingSystem {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model ServerType {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model Service {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model ServiceBackend {
    id: int64;
    name: string;
    activeFlag?: boolean = true;
  }
  model ServiceConfiguration {
    id: int64;
    key: string;
    value?: string;
  }
  model ServiceDependency {
    id: int64;
    name: string;
  }
  model ServiceLibrary {
    id: int64;
    name: string;
    version?: string;
  }
  model ServiceType {
    id: int64;
    name: string;
  }
  model VisualComponent {
    id: int64;
    name: string;
  }



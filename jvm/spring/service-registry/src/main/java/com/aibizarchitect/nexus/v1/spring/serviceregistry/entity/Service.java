package com.aibizarchitect.nexus.v1.spring.serviceregistry.entity;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.Objects;
import java.util.Set;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.OneToMany;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

@Entity
@Table(name = "services")
@JsonIgnoreProperties({ "hibernateLazyInitializer", "handler", "deployments", "serviceConfigs", "serviceDependenciesAsConsumer",
        "serviceDependenciesAsProvider", "subModules", "parentService" })
public class Service {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true)
    private String name;

    @Column(length = 1000)
    private String description;

    @ManyToOne(optional = false)
    @JoinColumn(name = "framework_id")
    private Framework framework;

    @ManyToOne(optional = false)
    @JoinColumn(name = "service_type_id")
    private ServiceType type;

    @ManyToOne(fetch = FetchType.EAGER)
    @JoinColumn(name = "component_override_id", referencedColumnName = "id", nullable = true)
    private VisualComponent componentOverride;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "parent_service_id")
    private Service parentService;

    @OneToMany(mappedBy = "parentService", fetch = FetchType.LAZY)
    private Set<Service> subModules = new HashSet<>();

    @Column(name = "default_port")
    private Integer defaultPort;

    @Column(name = "api_base_path")
    private String apiBasePath;

    /**
     * Verified per-service health-check path (audit thread 70d507dc).
     * Added by V10 migration. When null, {@link #getHealthCheckPath()} falls
     * back to the legacy derived "<apiBasePath>/actuator/health" so existing
     * rows and Spring Boot services keep their behavior.
     */
    @Column(name = "health_check_path")
    private String healthCheckPath;

    @Column(name = "repository_url")
    private String repositoryUrl;

    @Column
    private String version;

    @Column
    private String status;

    @Column(name = "active_flag")
    private Boolean activeFlag = true;

    // T25 1.2 (R-A-2026-08-15-008): provenance marker — seed (declarative catalog)
    // vs register (runtime-created via POST /api/v1/registry/register). The sync
    // job never overwrites catalog metadata of origin='seed' rows.
    @Column(name = "origin", length = 20)
    private String origin = "seed";

    @Column
    private LocalDateTime createdAt;

    @Column
    private LocalDateTime updatedAt;

    @OneToMany(mappedBy = "service", cascade = CascadeType.ALL, orphanRemoval = true)
    private Set<Deployment> deployments = new HashSet<>();

    @OneToMany(mappedBy = "service")
    private Set<ServiceConfiguration> serviceConfigs = new HashSet<>();

    @OneToMany(mappedBy = "service")
    private Set<ServiceDependency> serviceDependenciesAsConsumer = new HashSet<>();

    @OneToMany(mappedBy = "targetService")
    private Set<ServiceDependency> serviceDependenciesAsProvider = new HashSet<>();

    public Service() {
    }

    public Long getId() {
        return id;
    }

    public void setId(Long id) {
        this.id = id;
    }

    public String getName() {
        return name;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getDescription() {
        return description;
    }

    public void setDescription(String description) {
        this.description = description;
    }

    public Framework getFramework() {
        return framework;
    }

    public void setFramework(Framework framework) {
        this.framework = framework;
    }

    public ServiceType getType() {
        return type;
    }

    public void setType(ServiceType type) {
        this.type = type;
    }

    public VisualComponent getComponentOverride() {
        return componentOverride;
    }

    public void setComponentOverride(VisualComponent componentOverride) {
        this.componentOverride = componentOverride;
    }

    public Service getParentService() {
        return parentService;
    }

    public void setParentService(Service parentService) {
        this.parentService = parentService;
    }

    public Set<Service> getSubModules() {
        return subModules;
    }

    public void setSubModules(Set<Service> subModules) {
        this.subModules = subModules;
    }

    public Integer getDefaultPort() {
        return defaultPort;
    }

    public void setDefaultPort(Integer defaultPort) {
        this.defaultPort = defaultPort;
    }

    public String getApiBasePath() {
        return apiBasePath;
    }

    public void setApiBasePath(String apiBasePath) {
        this.apiBasePath = apiBasePath;
    }

    public String getRepositoryUrl() {
        return repositoryUrl;
    }

    public void setRepositoryUrl(String repositoryUrl) {
        this.repositoryUrl = repositoryUrl;
    }

    public String getVersion() {
        return version;
    }

    public void setVersion(String version) {
        this.version = version;
    }

    public String getStatus() {
        return status;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public Boolean getActiveFlag() {
        return activeFlag;
    }

    public void setActiveFlag(Boolean activeFlag) {
        this.activeFlag = activeFlag;
    }

    public String getOrigin() {
        return origin;
    }

    public void setOrigin(String origin) {
        this.origin = origin;
    }

    public LocalDateTime getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(LocalDateTime createdAt) {
        this.createdAt = createdAt;
    }

    public LocalDateTime getUpdatedAt() {
        return updatedAt;
    }

    public void setUpdatedAt(LocalDateTime updatedAt) {
        this.updatedAt = updatedAt;
    }

    public Set<Deployment> getDeployments() {
        return deployments;
    }

    public void setDeployments(Set<Deployment> deployments) {
        this.deployments = deployments;
    }

    public Set<ServiceConfiguration> getServiceConfigs() {
        return serviceConfigs;
    }

    public void setServiceConfigs(Set<ServiceConfiguration> serviceConfigs) {
        this.serviceConfigs = serviceConfigs;
    }

    public Set<ServiceDependency> getServiceDependenciesAsConsumer() {
        return serviceDependenciesAsConsumer;
    }

    public void setServiceDependenciesAsConsumer(Set<ServiceDependency> serviceDependenciesAsConsumer) {
        this.serviceDependenciesAsConsumer = serviceDependenciesAsConsumer;
    }

    public Set<ServiceDependency> getServiceDependenciesAsProvider() {
        return serviceDependenciesAsProvider;
    }

    public void setServiceDependenciesAsProvider(Set<ServiceDependency> serviceDependenciesAsProvider) {
        this.serviceDependenciesAsProvider = serviceDependenciesAsProvider;
    }

    // Backward-compatible ID accessors
    public Long getFrameworkId() {
        return framework != null ? framework.getId() : null;
    }

    public void setFrameworkId(Long frameworkId) {
        if (frameworkId != null) {
            this.framework = new Framework();
            this.framework.setId(frameworkId);
        }
    }

    public Long getServiceTypeId() {
        return type != null ? type.getId() : null;
    }

    public void setServiceTypeId(Long serviceTypeId) {
        if (serviceTypeId != null) {
            this.type = new ServiceType();
            this.type.setId(serviceTypeId);
        }
    }
    
    public Long getParentServiceId() {
        return parentService != null ? parentService.getId() : null;
    }

    public void setParentServiceId(Long parentServiceId) {
        if (parentServiceId != null) {
            this.parentService = new Service();
            this.parentService.setId(parentServiceId);
        } else {
            this.parentService = null;
        }
    }

    // --- Health check path (audit thread 70d507dc / health-path conformance) ---
    //
    // History: this field was never persisted. getHealthCheckPath() synthesized
    // "<apiBasePath>/actuator/health" and the setter was a no-op, so every
    // caller-supplied health path (POST /api/v1/services, PUT /api/v1/services/{id},
    // POST /api/v1/registry/register, broker sync) was silently discarded, and
    // Express-style services that actually serve /health (aegis-srv, nebula-srv,
    // cascade-srv, ...) were registered with a fabricated /actuator/health value.
    //
    // Now: explicit values are stored in health_check_path (V10) and win; the
    // legacy derived value remains only as a fallback for rows with nothing stored.

    /** Stored value if present, else the legacy derived "<apiBasePath>/actuator/health". */
    public String getHealthCheckPath() {
        if (healthCheckPath != null && !healthCheckPath.isBlank()) {
            return healthCheckPath;
        }
        return apiBasePath != null ? apiBasePath + "/actuator/health" : null;
    }

    /** Raw stored value without the derived fallback (for copy/update paths). */
    public String getHealthCheckPathRaw() {
        return healthCheckPath;
    }

    public void setHealthCheckPath(String healthCheckPath) {
        this.healthCheckPath = normalizeHealthCheckPath(healthCheckPath);
    }

    /**
     * Accepts a bare path ("/health"), a path with context ("/api/health"), or
     * a full URL ("http://host:port/health"). Values are trimmed; blank input
     * becomes null so the derived fallback applies. Full URLs keep URL
     * semantics (host pinning for off-host probing).
     */
    static String normalizeHealthCheckPath(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        return raw.trim();
    }

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = LocalDateTime.now();
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }

    @Override
    public boolean equals(Object o) {
        if (this == o)
            return true;
        if (!(o instanceof Service))
            return false;
        Service service = (Service) o;
        return Objects.equals(id, service.id) &&
                Objects.equals(name, service.name);
    }

    @Override
    public int hashCode() {
        return Objects.hash(id, name);
    }
}

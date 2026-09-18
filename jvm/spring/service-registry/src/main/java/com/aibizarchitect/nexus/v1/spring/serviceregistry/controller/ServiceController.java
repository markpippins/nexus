package com.aibizarchitect.nexus.v1.spring.serviceregistry.controller;

import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.aibizarchitect.nexus.v1.spring.serviceregistry.client.ServicesConsoleClient;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.entity.Service;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.entity.ServiceConfiguration;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.entity.ServiceDependency;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.entity.SystemServices;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.repository.ServiceConfigurationRepository;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.repository.ServiceDependencyRepository;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.repository.ServiceRepository;
import com.aibizarchitect.nexus.v1.spring.serviceregistry.repository.SystemServicesRepository;

@RestController
@RequestMapping("/api/v1/services")
public class ServiceController {

    private static final Logger log = LoggerFactory.getLogger(ServiceController.class);

    private final ServicesConsoleClient client;
    private final ServiceRepository serviceRepository;
    private final ServiceConfigurationRepository serviceConfigurationRepository;
    private final ServiceDependencyRepository serviceDependencyRepository;
    private final SystemServicesRepository systemServicesRepository;

    public ServiceController(ServicesConsoleClient client, ServiceRepository serviceRepository,
            ServiceConfigurationRepository serviceConfigurationRepository,
            ServiceDependencyRepository serviceDependencyRepository,
            SystemServicesRepository systemServicesRepository) {
        this.client = client;
        this.serviceRepository = serviceRepository;
        this.serviceConfigurationRepository = serviceConfigurationRepository;
        this.serviceDependencyRepository = serviceDependencyRepository;
        this.systemServicesRepository = systemServicesRepository;
    }

    @GetMapping
    public ResponseEntity<?> getServices(
            @RequestParam(required = false) String name,
            @RequestParam(required = false) Long frameworkId,
            @RequestParam(required = false) Boolean standalone,
            org.springframework.data.domain.Pageable pageable) {

        if (name != null) {
            log.info("Fetching service by name: {}", name);
            return serviceRepository.findByName(name)
                    .map(ResponseEntity::ok)
                    .orElse(ResponseEntity.notFound().build());
        } else if (frameworkId != null) {
            log.info("Fetching services by framework ID: {}", frameworkId);
            return ResponseEntity.ok(com.aibizarchitect.nexus.v1.spring.serviceregistry.dto.SpringPagedResponse.fromPage(serviceRepository.findByFramework_Id(frameworkId, pageable)));
        } else if (Boolean.TRUE.equals(standalone)) {
            log.info("Fetching standalone/parent services (parentService is null)");
            return ResponseEntity.ok(com.aibizarchitect.nexus.v1.spring.serviceregistry.dto.SpringPagedResponse.fromPage(serviceRepository.findByParentServiceIsNull(pageable)));
        } else {
            log.info("Fetching all services from database");
            return ResponseEntity.ok(com.aibizarchitect.nexus.v1.spring.serviceregistry.dto.SpringPagedResponse.fromPage(serviceRepository.findAll(pageable)));
        }
    }

    @GetMapping("/{id}")
    public ResponseEntity<Service> getServiceById(@PathVariable Long id) {
        log.info("Fetching service by ID: {}", id);
        Optional<Service> service = serviceRepository.findById(id);
        return service.map(ResponseEntity::ok)
                .orElse(ResponseEntity.notFound().build());
    }

    @GetMapping("/{id}/dependencies")
    public ResponseEntity<List<Service>> getServiceDependencies(@PathVariable Long id) {
        log.info("Fetching dependencies for service: {}", id);
        return ResponseEntity.ok(List.of());
    }

    @GetMapping("/{id}/dependents")
    public ResponseEntity<List<Service>> getServiceDependents(@PathVariable String id) {
        log.info("Fetching dependents for service: {}", id);
        return ResponseEntity.ok(List.of());
    }

    @GetMapping("/{id}/sub-modules")
    public ResponseEntity<List<Service>> getSubModules(@PathVariable Long id) {
        log.info("Fetching sub-modules for service: {}", id);
        List<Service> subModules = serviceRepository.findByParentService_Id(id);
        return ResponseEntity.ok(subModules);
    }

    @PostMapping
    public ResponseEntity<Service> createService(@RequestBody Service service) {
        log.info("Creating new service: {}", service.getName());

        if (serviceRepository.findByName(service.getName()).isPresent()) {
            log.warn("Service with name {} already exists", service.getName());
            return ResponseEntity.badRequest().build();
        }

        service.setActiveFlag(true);

        Service savedService = serviceRepository.save(service);
        log.info("Successfully created service with ID: {}", savedService.getId());
        java.net.URI location = org.springframework.web.servlet.support.ServletUriComponentsBuilder
                .fromCurrentRequest()
                .path("/{id}")
                .buildAndExpand(savedService.getId())
                .toUri();
        return ResponseEntity.created(location).body(savedService);
    }

    @PutMapping("/{id}")
    public ResponseEntity<Service> updateService(@PathVariable Long id, @RequestBody Service service) {
        log.info("Updating service with ID: {}", id);

        Optional<Service> existingServiceOpt = serviceRepository.findById(id);
        if (existingServiceOpt.isEmpty()) {
            log.warn("Service with ID {} not found", id);
            return ResponseEntity.notFound().build();
        }

        Service existingService = existingServiceOpt.get();
        if (!existingService.getName().equals(service.getName())) {
            if (serviceRepository.findByName(service.getName()).isPresent()) {
                log.warn("Service with name {} already exists", service.getName());
                return ResponseEntity.badRequest().build();
            }
        }

        existingService.setName(service.getName());
        existingService.setDescription(service.getDescription());
        existingService.setDefaultPort(service.getDefaultPort());
        existingService.setApiBasePath(service.getApiBasePath());
        existingService.setHealthCheckPath(service.getHealthCheckPathRaw());
        existingService.setRepositoryUrl(service.getRepositoryUrl());
        existingService.setVersion(service.getVersion());
        existingService.setStatus(service.getStatus());
        existingService.setActiveFlag(service.getActiveFlag());

        if (service.getFramework() != null) {
            existingService.setFramework(service.getFramework());
        }
        if (service.getType() != null) {
            existingService.setType(service.getType());
        }
        if (service.getParentService() != null) {
            existingService.setParentService(service.getParentService());
        }
        if (service.getComponentOverride() != null) {
            existingService.setComponentOverride(service.getComponentOverride());
        }

        Service updatedService = serviceRepository.save(existingService);
        log.info("Successfully updated service with ID: {}", id);
        return ResponseEntity.ok(updatedService);
    }

    @DeleteMapping("/{id}")
    @org.springframework.transaction.annotation.Transactional
    public ResponseEntity<Void> deleteService(@PathVariable Long id) {
        log.info("Deleting service with ID: {}", id);

        Optional<Service> serviceOpt = serviceRepository.findById(id);
        if (serviceOpt.isEmpty()) {
            log.warn("Service with ID {} not found", id);
            return ResponseEntity.notFound().build();
        }

        Service service = serviceOpt.get();

        // T27: clear uncascaded FK dependents before the hard delete.
        // registry.services is referenced by service_configs, service_dependencies
        // (both directions), system_services, and the self-FK parent_service_id.
        // Deployments cascade via the entity's orphanRemoval mapping.

        // 1. Promote children to top-level (avoid cascade-deleting whole subtrees)
        List<Service> subModules = serviceRepository.findByParentService_Id(id);
        if (!subModules.isEmpty()) {
            for (Service child : subModules) {
                child.setParentService(null);
            }
            serviceRepository.saveAll(subModules);
            log.info("Promoted {} sub-module(s) of service {} to top-level", subModules.size(), id);
        }

        // 2. Service configurations
        List<ServiceConfiguration> configs = serviceConfigurationRepository.findByServiceId(id);
        if (!configs.isEmpty()) {
            serviceConfigurationRepository.deleteAll(configs);
        }

        // 3. Service dependencies in both directions
        List<ServiceDependency> deps = new ArrayList<>();
        deps.addAll(serviceDependencyRepository.findByServiceId(id));
        deps.addAll(serviceDependencyRepository.findByTargetServiceId(id));
        if (!deps.isEmpty()) {
            serviceDependencyRepository.deleteAll(deps);
        }

        // 4. System-service mappings
        List<SystemServices> systemMappings = systemServicesRepository.findByServiceId(id);
        if (!systemMappings.isEmpty()) {
            systemServicesRepository.deleteAll(systemMappings);
        }

        serviceRepository.deleteById(id);
        log.info("Successfully deleted service with ID: {}", id);
        return ResponseEntity.noContent().build();
    }
}

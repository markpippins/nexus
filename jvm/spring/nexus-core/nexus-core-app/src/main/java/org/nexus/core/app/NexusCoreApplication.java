package org.nexus.core.app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.autoconfigure.domain.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

/**
 * Nexus Core — runnable read-only JVM projection monolith.
 *
 * Component-scans the projection modules (search, broker, solscript,
 * writequeue), the ported service kernels (aegis, shrapnel) and the PEB
 * admission kernel (org.nexus.peb) into a single Spring context on ONE port
 * (:8092). This is the failover/local posture: read surfaces served locally;
 * the pre-existing read-only controllers still 405 writes, while the ported
 * aegis/shrapnel surfaces and the peb admission facade are fully functional
 * (they own their own storage contracts).
 *
 * peb wiring mirrors PebApplication: entities in org.nexus.peb.domain.entity,
 * repositories in org.nexus.peb.store.repository. The REST surface
 * (AdmissionControllerFacade, POST /api/v1/peb/transaction) is unchanged.
 */
@SpringBootApplication
@ComponentScan(basePackages = {
        "org.nexus.core",
        "org.nexus.solscript",
        "org.nexus.writequeue",
        "org.nexus.peb",
})
@EntityScan(basePackages = "org.nexus.peb.domain.entity")
@EnableJpaRepositories(basePackages = "org.nexus.peb.store.repository")
public class NexusCoreApplication {

    public static void main(String[] args) {
        SpringApplication.run(NexusCoreApplication.class, args);
    }
}

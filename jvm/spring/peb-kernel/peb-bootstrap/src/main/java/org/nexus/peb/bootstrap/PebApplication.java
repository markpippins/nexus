package org.nexus.peb.bootstrap;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.persistence.autoconfigure.EntityScan;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;

@SpringBootApplication
@ComponentScan(basePackages = "org.nexus.peb")
@EntityScan(basePackages = "org.nexus.peb.domain.entity")
@EnableJpaRepositories(basePackages = "org.nexus.peb.store.repository")
public class PebApplication {

    public static void main(String[] args) {
        SpringApplication.run(PebApplication.class, args);
    }
}

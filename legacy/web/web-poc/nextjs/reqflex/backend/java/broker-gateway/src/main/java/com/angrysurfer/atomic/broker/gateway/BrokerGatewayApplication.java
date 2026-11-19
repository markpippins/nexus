package com.angrysurfer.atomic.broker.gateway;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.data.mongodb.repository.config.EnableMongoRepositories;
import org.springframework.data.jpa.repository.config.EnableJpaRepositories;
import org.springframework.boot.autoconfigure.domain.EntityScan;

@SpringBootApplication
@EnableMongoRepositories(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.user",
    "com.angrysurfer.atomic.fs",
    "com.angrysurfer.atomic.login",
    "com.angrysurfer.atomic.note",
    "com.angrysurfer.atomic.search",
    "com.angrysurfer.atomic.registry",
    "com.angrysurfer.atomic.admin.logging"
})
@EnableJpaRepositories(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.user",
    "com.angrysurfer.atomic.fs",
    "com.angrysurfer.atomic.login",
    "com.angrysurfer.atomic.note",
    "com.angrysurfer.atomic.search",
    "com.angrysurfer.atomic.registry",
    "com.angrysurfer.atomic.admin.logging"
})
@EntityScan(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.user",
    "com.angrysurfer.atomic.fs",
    "com.angrysurfer.atomic.login",
    "com.angrysurfer.atomic.note",
    "com.angrysurfer.atomic.search",
    "com.angrysurfer.atomic.registry",
    "com.angrysurfer.atomic.admin.logging"
})
@ComponentScan(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.user",
    "com.angrysurfer.atomic.fs",
    "com.angrysurfer.atomic.login",
    "com.angrysurfer.atomic.note",
    "com.angrysurfer.atomic.search",
    "com.angrysurfer.atomic.registry",
    "com.angrysurfer.atomic.admin.logging"
})
public class BrokerGatewayApplication {

	public static void main(String[] args) {
		SpringApplication.run(BrokerGatewayApplication.class, args);
	}

}
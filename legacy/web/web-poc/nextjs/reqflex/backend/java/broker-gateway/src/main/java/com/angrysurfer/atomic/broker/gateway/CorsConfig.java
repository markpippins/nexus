package com.angrysurfer.atomic.broker.gateway;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class CorsConfig {

    private static final Logger log = LoggerFactory.getLogger(CorsConfig.class);

    @Bean
    public WebMvcConfigurer corsConfigurer() {
        log.info("Initializing CORS Config - allowing all origins for external access");
        return new WebMvcConfigurer() {
            @Override
            public void addCorsMappings(CorsRegistry registry) {
                registry.addMapping("/**")
                        .allowedOriginPatterns("*")  // Allow all origins including external machines
                        .allowedMethods("GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD", "TRACE")
                        .allowedHeaders("*")
                        .allowCredentials(false)  // Must be false when using wildcard origins
                        .exposedHeaders("Authorization", "Content-Type", "X-Requested-With", "Link", "X-Total-Count")
                        .maxAge(3600);
            }
        };
    }
}

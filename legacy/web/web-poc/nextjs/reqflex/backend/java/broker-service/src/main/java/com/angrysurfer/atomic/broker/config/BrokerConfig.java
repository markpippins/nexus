package com.angrysurfer.atomic.broker.config;

import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;

/**
 * Configuration class to enable component scanning for the admin logging module.
 */
@Configuration
@ComponentScan(basePackages = {
    "com.angrysurfer.atomic.broker",
    "com.angrysurfer.atomic.admin.logging"
})
public class BrokerConfig {
    // This configuration enables component scanning for both broker and admin logging packages
}
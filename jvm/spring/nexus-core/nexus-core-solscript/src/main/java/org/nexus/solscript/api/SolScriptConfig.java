package org.nexus.solscript.api;

import org.nexus.solscript.port.InMemorySolStorage;
import org.nexus.solscript.port.SolStoragePort;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Provides a SolStoragePort; default in-memory until a datasource port is wired. */
@Configuration
public class SolScriptConfig {

    @Bean
    @ConditionalOnMissingBean(SolStoragePort.class)
    public SolStoragePort solStoragePort() {
        return new InMemorySolStorage();
    }
}
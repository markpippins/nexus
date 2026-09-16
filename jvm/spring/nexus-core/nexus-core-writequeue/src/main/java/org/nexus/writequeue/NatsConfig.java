package org.nexus.writequeue;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Lazy;

import io.nats.client.Connection;
import io.nats.client.JetStream;
import io.nats.client.Nats;
import io.nats.client.Options;

/**
 * NATS connection factory for the mobile write-queue.
 *
 * Graceful-degradation posture (mirrors python/cascade/nats_publisher.py):
 * the connection is LAZY — the write-queue must never hard-fail a caller
 * when NATS is unreachable (mobile / disconnected context). The producer
 * catches connect failures and falls back to a durable local buffer / log
 * rather than throwing into the REST layer.
 *
 * Connectivity is not assumed at bean-construction time: the bean creates
 * the Options/Nats.connect lazily on first write, so booting nexus-core
 * offline does not crash.
 */
@Configuration
public class NatsConfig {

    public static final String DEFAULT_NATS_URL = "nats://localhost:4222";

    @Bean
    @Lazy
    public NatsConnectionManager natsConnectionManager() {
        String url = System.getenv().getOrDefault("NATS_URL", DEFAULT_NATS_URL);
        return new NatsConnectionManager(url);
    }

    /**
     * Manages the (lazy, reconnectable) NATS connection + JetStream context.
     * Exists so the producer can grab/release on demand and tolerate NATS
     * being down without holding a stale connection bean.
     */
    public static class NatsConnectionManager {
        private final String url;
        private volatile Connection connection;
        private volatile JetStream jetStream;

        public NatsConnectionManager(String url) {
            this.url = url;
        }

        /** Lazily connect if not already connected. Returns null when NATS is unavailable. */
        public synchronized Connection connect() {
            if (connection != null && connection.getStatus() == Connection.Status.CONNECTED) {
                return connection;
            }
            try {
                Options opts = Options.builder()
                        .server(url)
                        .connectionName("nexus-core-writequeue")
                        .maxReconnects(1)
                        .build();
                connection = Nats.connect(opts);
                jetStream = connection.jetStream();
                return connection;
            } catch (Exception e) {
                connection = null;
                jetStream = null;
                return null;
            }
        }

        /** JetStream context for the current connection, or null. */
        public JetStream jetStream() {
            if (connection == null || connection.getStatus() != Connection.Status.CONNECTED) {
                return null;
            }
            return jetStream;
        }

        public String url() {
            return url;
        }
    }
}
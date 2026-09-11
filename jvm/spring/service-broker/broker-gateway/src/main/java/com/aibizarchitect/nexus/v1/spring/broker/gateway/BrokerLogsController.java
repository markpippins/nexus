package com.aibizarchitect.nexus.v1.spring.broker.gateway;

import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.CrossOrigin;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.Map;

@CrossOrigin(originPatterns = "*", allowedHeaders = "*", allowCredentials = "true", methods = { RequestMethod.GET,
        RequestMethod.OPTIONS })
@RestController
@RequestMapping("/api/v1/broker")
public class BrokerLogsController {

    private final BrokerTrafficStreamService brokerTrafficStreamService;

    public BrokerLogsController(BrokerTrafficStreamService brokerTrafficStreamService) {
        this.brokerTrafficStreamService = brokerTrafficStreamService;
    }

    @GetMapping(value = "/logs/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter streamBrokerLogs() {
        return brokerTrafficStreamService.subscribe();
    }

    /**
     * M1 traffic canary: cumulative per-service/operation invocation counts
     * since gateway boot ({startedAt, total, counts}). The zero-traffic
     * observation for cutover sign-off polls this endpoint: the target
     * service/operation count must not increase over the window. Counters
     * reset on restart (see startedAt) — windows must not span restarts.
     */
    @GetMapping(value = "/traffic/counts")
    public Map<String, Object> trafficCounts() {
        return brokerTrafficStreamService.trafficSnapshot();
    }
}

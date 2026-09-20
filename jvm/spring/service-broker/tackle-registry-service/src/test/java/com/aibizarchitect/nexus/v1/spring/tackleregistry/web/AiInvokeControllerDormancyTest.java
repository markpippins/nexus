package com.aibizarchitect.nexus.v1.spring.tackleregistry.web;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;

import com.aibizarchitect.nexus.v1.spring.tackleregistry.tackle.InferenceService;

/**
 * Issue 59bcd3da remediation, item 4 — the /ai/invoke dormancy guard.
 *
 * Pins the contract: the controller bean (and therefore the route) exists
 * ONLY when app.ai-invoke.enabled=true. Absent property (the deployed
 * default) = dormant; any other value = dormant. Explicit true = live.
 * This is the guard that keeps the SSRF-shaped, unauthenticated route from
 * silently becoming exposure.
 *
 * Plain ApplicationContextRunner: @ConditionalOnProperty evaluation needs
 * no web autoconfiguration — the bean-condition contract is what's pinned.
 */
class AiInvokeControllerDormancyTest {

    private final ApplicationContextRunner runner = new ApplicationContextRunner()
            .withBean("inference", InferenceService.class,
                      () -> Mockito.mock(InferenceService.class))
            .withUserConfiguration(AiInvokeController.class);

    @Test
    void controllerIsDormantByDefault() {
        // Property ABSENT — the deployed default. Dormancy must not depend
        // on anyone remembering to set a false value anywhere.
        runner.run(ctx -> assertThat(ctx).doesNotHaveBean("aiInvokeController"));
    }

    @Test
    void controllerIsDormantOnNonTrueValues() {
        runner.withPropertyValues("app.ai-invoke.enabled=false").run(ctx ->
                assertThat(ctx).doesNotHaveBean("aiInvokeController"));
        runner.withPropertyValues("app.ai-invoke.enabled=yes").run(ctx ->
                assertThat(ctx).doesNotHaveBean("aiInvokeController"));
    }

    @Test
    void controllerRegistersOnlyOnExplicitTrue() {
        runner.withPropertyValues("app.ai-invoke.enabled=true").run(ctx ->
                assertThat(ctx).hasSingleBean(AiInvokeController.class));
    }
}

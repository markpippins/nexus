package org.nexus.core.app.meep;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.nexus.core.app.NexusCoreApplication;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/**
 * Spring deployment-profile tests: the adapter translates HTTP ⇄ kernel
 * without owning pipeline logic. Kernel behavior itself is covered by
 * nexus-core-meep tests; these pin the HTTP contract.
 */
@SpringBootTest(classes = NexusCoreApplication.class)
@AutoConfigureMockMvc(addFilters = false)
class MeepControllerTest {

    @Autowired
    MockMvc mvc;

    private static String body(String prompt) {
        return "{\"prompt\":\"" + prompt + "\"}";
    }

    @Test
    void healthAnswers() throws Exception {
        mvc.perform(get("/api/meep/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.kernel").value("nexus-core-meep"));
    }

    @Test
    void classifyReturnsArchetypeAndDistribution() throws Exception {
        mvc.perform(post("/api/meep/classify").contentType(MediaType.APPLICATION_JSON).content(body("fix the bug")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.archetype").value("REVISION"))
                .andExpect(jsonPath("$.probabilities.REVISION").isNumber())
                .andExpect(jsonPath("$.classifierVersion").isNotEmpty());
    }

    @Test
    void compileReturnsFrozenGraphWithHash() throws Exception {
        mvc.perform(post("/api/meep/compile").contentType(MediaType.APPLICATION_JSON).content(body("build a service")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.schemaVersion").value("v1"))
                .andExpect(jsonPath("$.frozenAt").isNotEmpty())
                .andExpect(jsonPath("$.contentHash").isNotEmpty())
                .andExpect(jsonPath("$.topologicalOrder.length()").value(3));
    }

    @Test
    void executeReturnsHashChainedEventLog() throws Exception {
        mvc.perform(post("/api/meep/execute").contentType(MediaType.APPLICATION_JSON).content(body("build a service")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.eventCount").value(6))
                .andExpect(jsonPath("$.events[0].prevEventHash").value("genesis"))
                .andExpect(jsonPath("$.events.length()").value(6));
    }

    @Test
    void replayOfSuppliedEventsReconstructsState() throws Exception {
        String events = """
                {"events":[
                  {"eventId":"e1","timestamp":"t","executionId":"x","nodeId":"n1","eventType":"NODE_START","payload":{}},
                  {"eventId":"e2","timestamp":"t","executionId":"x","nodeId":"n1","eventType":"NODE_COMPLETE","payload":{}}
                ]}
                """;
        mvc.perform(post("/api/meep/replay").contentType(MediaType.APPLICATION_JSON).content(events))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.nodeStates.n1").value("COMPLETED"))
                .andExpect(jsonPath("$.complete").value(true))
                .andExpect(jsonPath("$.eventCount").value(2));
    }

    @Test
    void replayWithoutEventsStillAnswers() throws Exception {
        mvc.perform(post("/api/meep/replay").contentType(MediaType.APPLICATION_JSON).content(body("why did this happen")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.complete").value(true));
    }

    @Test
    void blankPromptIsRejected() throws Exception {
        mvc.perform(post("/api/meep/classify").contentType(MediaType.APPLICATION_JSON).content(body("  ")))
                .andExpect(status().isBadRequest());
    }
}

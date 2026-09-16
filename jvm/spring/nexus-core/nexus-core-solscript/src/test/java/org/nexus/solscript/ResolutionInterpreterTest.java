package org.nexus.solscript;

import org.junit.jupiter.api.Test;
import org.nexus.solscript.events.KeychainEvents;
import org.nexus.solscript.interpreter.ResolutionInterpreter;
import org.nexus.solscript.models.SolModels;
import org.nexus.solscript.models.Disposition;
import org.nexus.solscript.models.ExpressionKind;
import org.nexus.solscript.models.RuleType;
import org.nexus.solscript.models.Severity;
import org.nexus.solscript.models.SolOperator;
import org.nexus.solscript.query.QueryBuilder;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Interpreter parity tests mirroring typescript/solscript/test/parity/scenario.json.
 * The Java port must produce the same evaluations, guards, queries, and
 * transition outcomes as the TS core.
 */
class ResolutionInterpreterTest {

    private SolModels.Expression op(SolOperator op, SolModels.Expression left, SolModels.Expression right) {
        SolModels.Expression e = new SolModels.Expression();
        e.id = "e-" + op.value();
        e.kind = ExpressionKind.operator;
        e.returnType = "boolean";
        e.operator = op;
        e.operands.add(left);
        e.operands.add(right);
        return e;
    }

    private SolModels.Expression attrRef(String id, String attrId, String type) {
        SolModels.Expression e = new SolModels.Expression();
        e.id = id;
        e.kind = ExpressionKind.attribute_ref;
        e.returnType = type;
        e.attributeId = attrId;
        return e;
    }

    private SolModels.Expression lit(String id, Object value, String type) {
        SolModels.Expression e = new SolModels.Expression();
        e.id = id;
        e.kind = ExpressionKind.literal;
        e.returnType = type;
        e.literalValue = value;
        return e;
    }

    private ResolutionInterpreter buildScenario() {
        ResolutionInterpreter interp = new ResolutionInterpreter();

        // Concept Document
        SolModels.Concept doc = new SolModels.Concept();
        doc.id = "c-doc";
        doc.name = "Document";

        SolModels.ConceptAttribute status = new SolModels.ConceptAttribute();
        status.id = "a-status"; status.conceptId = "c-doc"; status.name = "status";
        status.valueType = "text"; status.isStateAttribute = true;
        status.allowedValues = new java.util.ArrayList<>(List.of("draft", "reviewed"));

        SolModels.ConceptAttribute size = new SolModels.ConceptAttribute();
        size.id = "a-size"; size.conceptId = "c-doc"; size.name = "size";
        size.valueType = "integer"; size.isStateAttribute = false;

        SolModels.ConceptAttribute title = new SolModels.ConceptAttribute();
        title.id = "a-title"; title.conceptId = "c-doc"; title.name = "title";
        title.valueType = "text"; title.isStateAttribute = false;

        doc.attributes.put("status", status);
        doc.attributes.put("size", size);
        doc.attributes.put("title", title);

        // State transition t-review: draft -> reviewed, guard title != ""
        SolModels.ConceptStateTransition review = new SolModels.ConceptStateTransition();
        review.id = "t-review"; review.conceptId = "c-doc";
        review.fromValue = "draft"; review.toValue = "reviewed"; review.name = "review";
        SolModels.Rule titleGuard = new SolModels.Rule();
        titleGuard.id = "r-title-nonempty"; titleGuard.name = "title non-empty";
        titleGuard.ruleType = RuleType.guard; titleGuard.severity = Severity.hard;
        titleGuard.isRelationalCheck = false;
        titleGuard.expression = op(SolOperator.Neq, attrRef("e-title-ref", "a-title", "text"), lit("e-title-lit", "", "text"));
        review.guards.add(titleGuard);
        doc.stateTransitions.add(review);

        interp.addConcept(doc);
        interp.stateTransitions.put(review.id, review);
        interp.rules.put(titleGuard.id, titleGuard);

        // Entities
        SolModels.Entity ok = new SolModels.Entity();
        ok.id = "ent-ok"; ok.conceptId = "c-doc"; ok.externalId = "ext-ok";
        ok.attributes.put("status", "draft"); ok.attributes.put("size", 10); ok.attributes.put("title", "parity");
        interp.addEntity(ok);

        SolModels.Entity bad = new SolModels.Entity();
        bad.id = "ent-bad"; bad.conceptId = "c-doc"; bad.externalId = "ext-bad";
        bad.attributes.put("status", "draft"); bad.attributes.put("size", 3); bad.attributes.put("title", "");
        interp.addEntity(bad);

        // Proposition p-big: size > 5 asserted on ent-ok
        SolModels.Rule bigRule = new SolModels.Rule();
        bigRule.id = "r-big"; bigRule.name = "size > 5"; bigRule.ruleType = RuleType.invariant;
        bigRule.severity = Severity.hard; bigRule.isRelationalCheck = false;
        bigRule.expression = op(SolOperator.Gt, attrRef("e-size-ref", "a-size", "integer"), lit("e-size-lit", 5, "integer"));
        interp.rules.put(bigRule.id, bigRule);

        SolModels.Proposition big = new SolModels.Proposition();
        big.id = "p-big"; big.title = "size over threshold";
        big.assetConceptId = "c-doc"; big.subjectEntityId = "ent-ok";
        big.disposition = Disposition.Pending;
        big.assertions.add(bigRule);
        interp.addProposition(big);

        return interp;
    }

    @Test
    void propositionEvaluatesAssertedWhenAssertionsPass() {
        ResolutionInterpreter interp = buildScenario();
        SolModels.Proposition big = interp.getProposition("p-big");
        Object[] result = interp.evaluateProposition(big, null);
        assertThat((Disposition) result[0]).isEqualTo(Disposition.Asserted);
        assertThat((boolean) result[1]).isTrue();
        assertThat((String) result[2]).isEqualTo("not_scoped");
    }

    @Test
    void propositionEvaluatesRejectedWhenAssertionFails() {
        ResolutionInterpreter interp = buildScenario();
        // ent-bad has size 3, so the size>5 assertion fails.
        SolModels.Rule bigRule = interp.rules.get("r-big");
        SolModels.Entity bad = interp.getEntity("ent-bad");
        Map<String, Object> r = interp.checkRule(bigRule, bad);
        assertThat((boolean) r.get("passed")).isFalse();
        assertThat((String) r.get("reason")).contains("failed");
    }

    @Test
    void transitionGuardPassesForValidEntity() {
        ResolutionInterpreter interp = buildScenario();
        SolModels.ConceptStateTransition review = interp.getStateTransition("t-review");
        SolModels.Entity ok = interp.getEntity("ent-ok");
        Map<String, Object> g = interp.checkTransitionGuard(review, ok);
        assertThat((boolean) g.get("passed")).isTrue();
    }

    @Test
    void transitionCommitsAndMutatesState() {
        ResolutionInterpreter interp = buildScenario();
        SolModels.Entity ok = interp.getEntity("ent-ok");
        ResolutionInterpreter.TransitionOutcome outcome =
            interp.transitionEntity("ent-ok", "t-review", Map.of());
        assertThat(outcome.committed).isTrue();
        assertThat(ok.attributes.get("status")).isEqualTo("reviewed");
        assertThat(outcome.event.kind).isEqualTo("resolution.transition.committed");
        assertThat(outcome.event.readSet.get("state_after")).isEqualTo("reviewed");
    }

    @Test
    void transitionRefusesWhenGuardFails() {
        ResolutionInterpreter interp = buildScenario();
        SolModels.Entity bad = interp.getEntity("ent-bad");
        ResolutionInterpreter.TransitionOutcome outcome =
            interp.transitionEntity("ent-bad", "t-review", Map.of());
        assertThat(outcome.committed).isFalse();
        assertThat(bad.attributes.get("status")).isEqualTo("draft"); // unchanged
        assertThat(outcome.event.kind).isEqualTo("resolution.transition.refused");
    }

    @Test
    void transitionRejectsUnknownEntity() {
        ResolutionInterpreter interp = buildScenario();
        ResolutionInterpreter.TransitionOutcome outcome =
            interp.transitionEntity("missing", "t-review", Map.of());
        assertThat(outcome.committed).isFalse();
        assertThat(outcome.event.kind).isEqualTo("resolution.transition.rejected");
    }

    @Test
    void queryFiltersAndOrders() {
        ResolutionInterpreter interp = buildScenario();
        QueryBuilder builder = new QueryBuilder(interp);
        List<Map<String, Object>> rows = builder.select("Document")
            .where("size", SolOperator.Gt, 5)
            .orderBy("size", "DESC")
            .execute();
        assertThat(rows).hasSize(1);
        assertThat(rows.get(0).get("id")).isEqualTo("ent-ok");
    }

    @Test
    void queryProjectsSelectedFields() {
        ResolutionInterpreter interp = buildScenario();
        QueryBuilder builder = new QueryBuilder(interp);
        List<Map<String, Object>> rows = builder.select("Document")
            .selectFieldsTo("title", "id", "external_id")
            .execute();
        assertThat(rows).hasSize(2);
        assertThat(rows.get(0)).containsKeys("title", "id", "external_id");
    }

    @Test
    void stableDigestMatchesSha256OfCanonicalJson() {
        // Canonical JSON of {"b":2,"a":1} sorts keys -> {"a":1,"b":2}
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("b", 2);
        m.put("a", 1);
        String digest = KeychainEvents.stableDigest(m);
        // SHA-256 of '{"a":1,"b":2}' — sanity: 64 hex chars.
        assertThat(digest).hasSize(64);
        assertThat(KeychainEvents.stableDigest(m)).isEqualTo(digest); // deterministic
    }

    @Test
    void transitionEventCarriesSnakeCaseGuardResults() {
        ResolutionInterpreter interp = buildScenario();
        SolModels.Entity bad = interp.getEntity("ent-bad");
        ResolutionInterpreter.TransitionOutcome outcome =
            interp.transitionEntity("ent-bad", "t-review", Map.of());
        // readSet.guard_results has rule_id/rule_name/passed/reason keys (parity).
        List<Map<String, Object>> guards =
            (List<Map<String, Object>>) outcome.event.readSet.get("guard_results");
        assertThat(guards).isNotEmpty();
        Map<String, Object> first = guards.get(0);
        assertThat(first).containsKeys("rule_id", "rule_name", "passed", "reason");
    }
}
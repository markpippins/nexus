package org.nexus.core.meep.classifier;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;
import org.nexus.core.meep.ast.Ast;
import org.nexus.core.meep.model.Models;

/**
 * Station 1: deterministic keyword-heuristic IRL classifier
 * (Python reference: meep.irl_classifier). Keyword sets, the DEFAULT
 * standing reserve, AST structural bonuses, and version strings mirror
 * the reference exactly.
 */
public final class IrlClassifier {
    private static final Map<String, List<String>> KEYWORDS = keywords();
    private IrlClassifier() {}

    public static Models.IrlResult classify(String prompt, Ast.Features features) {
        Map<String, Double> scores = scores(prompt.toLowerCase());
        String version = "heuristic-v1";
        if (features != null && features.structural()) {
            version = "heuristic-v1+ast";
            String headings = features.headingText().toLowerCase();
            KEYWORDS.forEach((kind, words) -> scores.put(kind, scores.get(kind) + count(headings, words)));
            scores.put("CONSTRUCTION", scores.get("CONSTRUCTION") + (features.hasCodeBlocks() ? 1.0 : 0)
                    + (features.hasHeadings() ? 1.0 : 0) + (features.hasLists() ? 0.5 : 0)
                    + (features.longDocument() ? 0.5 : 0));
        }
        scores.put("DEFAULT", 1.0);
        double total = scores.values().stream().mapToDouble(Double::doubleValue).sum();
        Map<String, Double> probabilities = new LinkedHashMap<>();
        scores.forEach((key, value) -> probabilities.put(key, value / total));
        return new Models.IrlResult(Map.copyOf(probabilities), prompt, version);
    }

    private static Map<String, Double> scores(String prompt) {
        Map<String, Double> out = new LinkedHashMap<>();
        KEYWORDS.forEach((kind, words) -> out.put(kind, (double) count(prompt, words)));
        return out;
    }

    private static int count(String text, List<String> words) {
        int result = 0;
        for (String word : words) {
            if (word.contains(" ")) { if (text.contains(word)) result++; }
            else if (Pattern.compile("\\b" + Pattern.quote(word) + "\\b").matcher(text).find()) result++;
        }
        return result;
    }

    private static Map<String, List<String>> keywords() {
        Map<String, List<String>> m = new LinkedHashMap<>();
        m.put("CONSTRUCTION", List.of("build", "create", "make", "implement", "construct", "add", "new", "develop", "establish", "setup", "scaffold", "generate", "write", "produce", "assemble", "configure"));
        m.put("EXECUTION", List.of("run", "execute", "do", "perform", "process", "start", "deploy", "launch", "trigger", "invoke", "proceed", "go", "apply", "activate", "engage"));
        m.put("REFLECTION", List.of("why", "analyze", "reflect", "think", "understand", "explain", "happen", "happened", "investigate", "meaning", "root cause", "reason", "what happened", "how did", "what does", "what caused", "diagnose", "trace"));
        m.put("RECONCILIATION", List.of("reconcile", "merge", "align", "harmonize", "unify", "integrate", "bring together", "resolve conflict", "synchronize", "consolidate", "mediate"));
        m.put("REVISION", List.of("fix", "change", "update", "revise", "modify", "correct", "improve", "refactor", "bug", "patch", "error", "issue", "rewrite", "redo", "amend", "edit", "adjust", "repair"));
        m.put("COUNTERFACTUAL", List.of("what if", "imagine", "alternative", "hypothetical", "could have", "might have", "suppose", "what would", "what could", "otherwise"));
        m.put("AUDIT", List.of("audit", "check", "verify", "validate", "inspect", "assess", "compliance", "review compliance", "examine", "confirm"));
        m.put("COMPRESSION", List.of("compress", "summarize", "summarise", "condense", "shorten", "extract", "reduce", "overview", "tl;dr", "brief", "digest", "synopsis"));
        m.put("CONSTRAINT_INJECTION", List.of("constrain", "limit", "restrict", "safe", "guard", "bound", "permission", "authorize", "secure", "sanitize", "validate input", "safety", "permit", "allowlist"));
        return Map.copyOf(m);
    }
}

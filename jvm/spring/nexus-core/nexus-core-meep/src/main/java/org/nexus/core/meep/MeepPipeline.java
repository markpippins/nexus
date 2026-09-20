package org.nexus.core.meep;

import java.time.Clock;
import org.nexus.core.meep.ast.Ast;
import org.nexus.core.meep.classifier.IrlClassifier;
import org.nexus.core.meep.compiler.ResolverCompiler;
import org.nexus.core.meep.execution.Execution;
import org.nexus.core.meep.lowering.Lowering;
import org.nexus.core.meep.model.CerLog;
import org.nexus.core.meep.model.Models;

/**
 * Pipeline orchestration (Python reference: meep.pipeline).
 * Stations: AST → IRL → IR → spec compiler → lowering (freeze) → scheduler.
 * Replay is a consumer, not an inline station.
 */
public final class MeepPipeline {
    private MeepPipeline() {}

    public static CerLog run(String prompt) { return run(prompt, Clock.systemUTC(), true); }

    public static CerLog run(String prompt, Clock clock, boolean useAst) {
        Ast.Features features = useAst ? Ast.features(Ast.parse(prompt)) : null;
        Models.IrlResult classified = IrlClassifier.classify(prompt, features);
        Models.IrSelection selected = ResolverCompiler.resolve(classified);
        Models.WorkRequestGraph work = ResolverCompiler.compile(selected, prompt);
        Models.ExecutionGraph execution = Lowering.lower(work, clock);
        return Execution.schedule(execution, clock);
    }

    public static Models.ExecutionState runAndReplay(String prompt, Clock clock) {
        return Execution.replay(run(prompt, clock, true));
    }
}

import { digestJson } from './phase-a';

export interface RevisionState {
  id: string;
  name: string;
  is_initial?: boolean;
  is_terminal?: boolean;
}

export interface RevisionTransition {
  id: string;
  name: string;
  from_state_id?: string | null;
  to_state_id?: string | null;
}

export interface WindTaskMapping {
  state_id: string;
  wind_task_id: string;
  is_check_only: boolean;
}

export interface WindOutcomeMapping {
  transition_id: string;
  wind_task_id: string;
  wind_outcome_id: string;
}

export interface WindTaskRef {
  id: string;
  name?: string;
  tackle_task_id?: string | null;
}

export interface WindOutcomeRef {
  id: string;
  task_id: string;
  code?: string;
}

export interface WindNodePlan {
  state_id: string;
  wind_task_id: string;
  name: string;
  is_entrypoint: boolean;
  is_terminal: boolean;
}

export interface WindEdgePlan {
  transition_id: string;
  from_node_state_id: string;
  from_task_id: string;
  outcome_id: string;
  to_node_state_id: string;
}

export interface WindCompilationPlan {
  nodes: WindNodePlan[];
  edges: WindEdgePlan[];
  graph_digest: string;
  errors: string[];
}

/**
 * Validate the revision-scoped bridge and build the deterministic Wind graph
 * plan. This function has no database or filesystem dependencies so it can be
 * used for dry-runs and hermetic tests.
 *
 * The bridge deliberately consumes pre-existing Wind tasks/outcomes referenced
 * by immutable mapping rows. It creates the workflow version, nodes, and edges;
 * task/outcome provisioning remains a separate design-time operation because
 * those rows carry Wind ownership/title/agent semantics that Aegis must not
 * invent.
 */
export function buildWindCompilationPlan(
  revisionId: string,
  model: Record<string, any>,
  taskMappings: WindTaskMapping[],
  outcomeMappings: WindOutcomeMapping[],
  windTasks: WindTaskRef[],
  windOutcomes: WindOutcomeRef[],
): WindCompilationPlan {
  const errors: string[] = [];
  const states: RevisionState[] = Array.isArray(model.states) ? model.states : [];
  const transitions: RevisionTransition[] = Array.isArray(model.transitions) ? model.transitions : [];
  const stateById = new Map<string, RevisionState>();
  const taskMappingByState = new Map<string, WindTaskMapping>();
  const outcomeMappingByTransition = new Map<string, WindOutcomeMapping>();
  const taskById = new Map(windTasks.map((task) => [task.id, task]));
  const outcomeById = new Map(windOutcomes.map((outcome) => [outcome.id, outcome]));

  for (const state of states) {
    if (!state?.id || !state.name) {
      errors.push('revision contains a state without an id and name');
      continue;
    }
    if (stateById.has(state.id)) errors.push(`revision contains duplicate state ${state.id}`);
    stateById.set(state.id, state);
  }

  for (const mapping of taskMappings) {
    if (taskMappingByState.has(mapping.state_id)) {
      errors.push(`multiple Wind task mappings exist for state ${mapping.state_id}`);
    }
    taskMappingByState.set(mapping.state_id, mapping);
    if (!stateById.has(mapping.state_id)) {
      errors.push(`task mapping references state ${mapping.state_id} outside revision ${revisionId}`);
    }
    if (!taskById.has(mapping.wind_task_id)) {
      errors.push(`task mapping references missing Wind task ${mapping.wind_task_id}`);
    }
    const task = taskById.get(mapping.wind_task_id);
    const expectedCheckOnly = task ? task.tackle_task_id == null : false;
    if (task && mapping.is_check_only !== expectedCheckOnly) {
      errors.push(`task mapping for state ${mapping.state_id} disagrees with Wind task ${mapping.wind_task_id} check-only semantics`);
    }
  }

  for (const state of states) {
    if (!taskMappingByState.has(state.id)) {
      errors.push(`state ${state.name} (${state.id}) has no revision-scoped Wind task mapping`);
    }
  }

  for (const mapping of outcomeMappings) {
    if (outcomeMappingByTransition.has(mapping.transition_id)) {
      errors.push(`multiple Wind outcome mappings exist for transition ${mapping.transition_id}`);
    }
    outcomeMappingByTransition.set(mapping.transition_id, mapping);
    const transition = transitions.find((candidate) => candidate.id === mapping.transition_id);
    if (!transition) {
      errors.push(`outcome mapping references transition ${mapping.transition_id} outside revision ${revisionId}`);
    }
    const outcome = outcomeById.get(mapping.wind_outcome_id);
    if (!outcome) {
      errors.push(`outcome mapping references missing Wind outcome ${mapping.wind_outcome_id}`);
    } else if (outcome.task_id !== mapping.wind_task_id) {
      errors.push(`outcome ${mapping.wind_outcome_id} does not belong to mapped task ${mapping.wind_task_id}`);
    }
    if (!taskById.has(mapping.wind_task_id)) {
      errors.push(`outcome mapping references missing Wind task ${mapping.wind_task_id}`);
    }
  }

  const nodes: WindNodePlan[] = states.map((state) => {
    const mapping = taskMappingByState.get(state.id);
    return {
      state_id: state.id,
      wind_task_id: mapping?.wind_task_id || '',
      name: state.name,
      is_entrypoint: state.is_initial === true,
      is_terminal: state.is_terminal === true,
    };
  });

  const initialStates = states.filter((state) => state.is_initial === true);
  if (initialStates.length !== 1) {
    errors.push(`Wind compilation requires exactly one initial state; found ${initialStates.length}`);
  }

  const edges: WindEdgePlan[] = [];
  for (const transition of transitions) {
    if (!transition.from_state_id || !transition.to_state_id) {
      errors.push(`transition ${transition.name} (${transition.id}) must have both from_state_id and to_state_id`);
      continue;
    }
    if (!stateById.has(transition.from_state_id) || !stateById.has(transition.to_state_id)) {
      errors.push(`transition ${transition.name} (${transition.id}) references a state outside revision ${revisionId}`);
      continue;
    }
    const taskMapping = taskMappingByState.get(transition.from_state_id);
    const outcomeMapping = outcomeMappingByTransition.get(transition.id);
    if (!taskMapping || !outcomeMapping) {
      if (!outcomeMapping) errors.push(`transition ${transition.name} (${transition.id}) has no revision-scoped Wind outcome mapping`);
      continue;
    }
    if (outcomeMapping.wind_task_id !== taskMapping.wind_task_id) {
      errors.push(`transition ${transition.name} (${transition.id}) outcome task does not match its source state task`);
      continue;
    }
    edges.push({
      transition_id: transition.id,
      from_node_state_id: transition.from_state_id,
      from_task_id: taskMapping.wind_task_id,
      outcome_id: outcomeMapping.wind_outcome_id,
      to_node_state_id: transition.to_state_id,
    });
  }

  const graphDigestInput = {
    revision_id: revisionId,
    nodes: nodes.map(({ state_id, wind_task_id, name, is_entrypoint, is_terminal }) => ({
      state_id, wind_task_id, name, is_entrypoint, is_terminal,
    })),
    edges: edges.map(({ transition_id, from_node_state_id, from_task_id, outcome_id, to_node_state_id }) => ({
      transition_id, from_node_state_id, from_task_id, outcome_id, to_node_state_id,
    })),
  };

  return {
    nodes,
    edges,
    graph_digest: digestJson(graphDigestInput),
    errors: [...new Set(errors)],
  };
}

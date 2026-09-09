import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { buildWindCompilationPlan } = require('../dist/wind-compiler.js');

const REVISION = '11111111-1111-4111-8111-111111111111';
const stateA = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const stateB = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const transition = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const taskA = 'aaaaaaaa-0000-4000-8000-aaaaaaaaaaaa';
const taskB = 'bbbbbbbb-0000-4000-8000-bbbbbbbbbbbb';
const outcome = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';

test('builds deterministic Wind nodes and edges from a complete revision mapping', () => {
  const input = {
    revision: {
      states: [
        { id: stateA, name: 'DRAFT', is_initial: true, is_terminal: false },
        { id: stateB, name: 'APPROVED', is_initial: false, is_terminal: true },
      ],
      transitions: [{ id: transition, name: 'approve', from_state_id: stateA, to_state_id: stateB }],
    },
    tasks: [
      { id: taskA, name: 'draft-check', tackle_task_id: null },
      { id: taskB, name: 'approve-worker', tackle_task_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee' },
    ],
    outcomes: [{ id: outcome, task_id: taskA, code: 'PASS' }],
  };
  const first = buildWindCompilationPlan(
    REVISION,
    input.revision,
    [
      { state_id: stateA, wind_task_id: taskA, is_check_only: true },
      { state_id: stateB, wind_task_id: taskB, is_check_only: false },
    ],
    [{ transition_id: transition, wind_task_id: taskA, wind_outcome_id: outcome }],
    input.tasks,
    input.outcomes,
  );
  const second = buildWindCompilationPlan(
    REVISION,
    input.revision,
    [
      { state_id: stateA, wind_task_id: taskA, is_check_only: true },
      { state_id: stateB, wind_task_id: taskB, is_check_only: false },
    ],
    [{ transition_id: transition, wind_task_id: taskA, wind_outcome_id: outcome }],
    input.tasks,
    input.outcomes,
  );

  assert.deepEqual(first.errors, []);
  assert.equal(first.nodes.length, 2);
  assert.equal(first.edges.length, 1);
  assert.equal(first.edges[0].outcome_id, outcome);
  assert.equal(first.graph_digest, second.graph_digest);
});

test('requires every transition to resolve to a source-task outcome', () => {
  const plan = buildWindCompilationPlan(
    REVISION,
    {
      states: [
        { id: stateA, name: 'DRAFT', is_initial: true },
        { id: stateB, name: 'APPROVED', is_terminal: true },
      ],
      transitions: [{ id: transition, name: 'approve', from_state_id: stateA, to_state_id: stateB }],
    },
    [
      { state_id: stateA, wind_task_id: taskA, is_check_only: true },
      { state_id: stateB, wind_task_id: taskB, is_check_only: false },
    ],
    [],
    [
      { id: taskA, tackle_task_id: null },
      { id: taskB, tackle_task_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee' },
    ],
    [],
  );
  assert.ok(plan.errors.some((error) => error.includes('no revision-scoped Wind outcome mapping')));
  assert.equal(plan.edges.length, 0);
});

test('refuses incomplete or semantically mismatched mappings', () => {
  const plan = buildWindCompilationPlan(
    REVISION,
    {
      states: [
        { id: stateA, name: 'DRAFT', is_initial: true },
        { id: stateB, name: 'APPROVED', is_terminal: true },
      ],
      transitions: [{ id: transition, name: 'approve', from_state_id: stateA, to_state_id: stateB }],
    },
    [{ state_id: stateA, wind_task_id: taskA, is_check_only: false }],
    [{ transition_id: transition, wind_task_id: taskB, wind_outcome_id: outcome }],
    [{ id: taskA, tackle_task_id: null }],
    [{ id: outcome, task_id: taskA }],
  );

  assert.ok(plan.errors.some((error) => error.includes('check-only semantics')));
  assert.ok(plan.errors.some((error) => error.includes('no revision-scoped Wind task mapping')));
  assert.ok(plan.errors.some((error) => error.includes('does not belong to mapped task')));
  assert.notEqual(plan.errors.length, 0);
});

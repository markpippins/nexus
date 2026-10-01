import { Router } from 'express';
import { query } from '../db.js';
import { BadRequestError } from '../errors.js';

export const validateRouter = Router();

// Validate a workflow version's graph integrity
// Uses the v_workflow_graph_validation view
validateRouter.get('/:version_id', async (req, res, next) => {
  try {
    const result = await query(
      `SELECT workflow_version_id, issue_type, node_id, details
       FROM wind.v_workflow_graph_validation
       WHERE workflow_version_id = $1
       ORDER BY issue_type, node_id`,
      [req.params.version_id]
    );

    const issues = result.rows;
    const valid = issues.length === 0;

    res.json({
      version_id: req.params.version_id,
      valid,
      issue_count: issues.length,
      issues,
    });
  } catch (err) { next(err); }
});

// Validate a workflow version has required structure
// Checks: at least one entrypoint, at least one terminal, no orphaned nodes
validateRouter.post('/:version_id/structure', async (req, res, next) => {
  try {
    const versionId = req.params.version_id;
    const checks = [];

    // Check entrypoint count
    const epResult = await query(
      'SELECT COUNT(*) AS cnt FROM wind.workflow_nodes WHERE workflow_version_id = $1 AND is_entrypoint = true',
      [versionId]
    );
    const entrypoints = parseInt(epResult.rows[0].cnt);
    checks.push({
      check: 'has_entrypoint',
      pass: entrypoints === 1,
      detail: entrypoints === 0 ? 'No entrypoint found' : entrypoints === 1 ? 'OK' : `${entrypoints} entrypoints found (expected 1)`,
    });

    // Check terminal count
    const termResult = await query(
      'SELECT COUNT(*) AS cnt FROM wind.workflow_nodes WHERE workflow_version_id = $1 AND is_terminal = true',
      [versionId]
    );
    const terminals = parseInt(termResult.rows[0].cnt);
    checks.push({
      check: 'has_terminal',
      pass: terminals >= 1,
      detail: terminals === 0 ? 'No terminal node found' : `${terminals} terminal node(s)`,
    });

    // Check non-terminal nodes have at least one outgoing edge
    const noEdgeResult = await query(
      `SELECT n.id, n.name
       FROM wind.workflow_nodes n
       WHERE n.workflow_version_id = $1
         AND n.is_terminal = false
         AND n.id NOT IN (SELECT from_node_id FROM wind.workflow_edges WHERE workflow_version_id = $1)`,
      [versionId]
    );
    checks.push({
      check: 'non_terminal_has_edges',
      pass: noEdgeResult.rows.length === 0,
      detail: noEdgeResult.rows.length === 0
        ? 'OK'
        : `Non-terminal nodes without outgoing edges: ${noEdgeResult.rows.map(r => r.name).join(', ')}`,
    });

    const valid = checks.every(c => c.pass);

    res.json({ version_id: versionId, valid, checks });
  } catch (err) { next(err); }
});

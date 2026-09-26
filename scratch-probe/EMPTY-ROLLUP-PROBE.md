# Empty-rollup detection probe

Throwaway branch used to verify that `bin/merge_pr.py` emits the
`no CI checks reported (fail closed)` marker against a real GitHub PR whose
`statusCheckRollup` is genuinely empty.

This branch name is deliberately outside every `push.branches` filter
(`main`, `master`, `dev`, `feature/*`) and the PR base is likewise outside
every `pull_request.branches` filter, so no workflow fires. The only
unfiltered `pull_request` workflow, `broker-e2e.yml`, is dodged because this
file matches none of its `paths:` globs.

No product code is touched. Delete after the probe.

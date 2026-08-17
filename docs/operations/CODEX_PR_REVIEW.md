# Codex strict pull-request review

`.github/workflows/pr-code-review.yml` runs Codex as a production-safety gate on
non-draft pull requests targeting `main`. It reviews the complete PR diff in an
ephemeral read-only sandbox and returns schema-constrained findings.

## Authentication

Create an Actions repository secret named `CODEX_AUTH_JSON` whose value is the
complete contents of the Codex CLI `auth.json` file. Do not base64-encode it and
do not commit it. The workflow writes the value to
`$RUNNER_TEMP/codex-home/auth.json` with a restrictive umask, validates that it
is JSON, and deletes it in an `always()` cleanup step.

Treat this file as a credential. Rotate it if it appears in logs, artifacts,
commits, caches, or pull-request content.

## Review behavior

- Draft PRs are not reviewed until marked ready.
- A new commit cancels the obsolete review and starts a new one.
- Codex CLI is pinned to `0.147.0` for reproducibility.
- The job installs `bubblewrap` and loads Ubuntu 24.04's extra AppArmor profile
  so Codex can create the user namespace required by its Linux sandbox.
- Repository/user Codex configuration is ignored; authentication still comes
  from the temporary `CODEX_HOME`.
- Codex cannot modify the checkout because the sandbox is read-only.
- PASS requires an empty findings list.
- P0, P1, or P2 findings produce FAIL and block the check.
- Missing authentication, a Codex crash, empty output, malformed JSON, or an
  inconsistent verdict fails closed.
- A single bot comment is updated on each run to avoid comment spam.

The workflow uploads the structured result, rendered comment, and Codex process
log for seven days. Artifacts are diagnostics and must not contain `auth.json`.

## Public-repository limitation

GitHub does not provide repository secrets to workflows triggered from forks.
Fork PRs therefore cannot pass this credentialed check until a trusted maintainer
recreates the change on a branch in the repository or uses a separately approved
review process. Do not switch this workflow to `pull_request_target` and execute
untrusted PR code with secrets.

# Codex autonomous implementation and repair

Orbit uses one write-capable Codex workflow with separate jobs for autonomous
issue implementation and requested-change repair. Both jobs create reviewable
commits; neither merges changes.

## Authentication and environments

Create an Actions repository secret named `CODEX_AUTH_JSON` containing the full,
unencoded contents of Codex CLI `auth.json`. Configure a GitHub environment named
`Codex-Automation` and require a trusted reviewer before jobs can start.

The workflow writes the credential to `$RUNNER_TEMP/codex-home/auth.json` with a
restrictive umask, validates it as JSON, and removes it in an `always()` cleanup
step. Codex CLI is pinned to `0.147.0` and runs ephemerally in a
`workspace-write` sandbox.

## Autonomous issue implementation

The `autonomous` job in `.github/workflows/codex-automation.yml` starts when the
`ai-autonomous` label is added to an issue. Environment approval is required
before the job can access the credential. The job:

1. Checks out `main` and creates `codex/issue-<number>-<slug>`.
2. Supplies the issue to Codex as explicitly untrusted task data.
3. Lets Codex edit and test the workspace without GitHub credentials; checkout
   persistence is disabled and push authentication is configured afterward.
4. Commits and pushes changes only after Codex exits successfully.
5. Opens a pull request targeting `main`, applies the `ai-autonomous` and
   `codex` labels, and requests human review.

Removing and re-adding the label retries the workflow. Only one run per issue
is allowed at a time. If the generated branch already exists, delete or rename
that branch before retrying.

## Requested-change repair

The `repair` job in `.github/workflows/codex-automation.yml` starts when a review
submits `changes_requested`. For security and push correctness, it runs only
when the pull-request branch belongs to this repository; fork pull requests are
skipped. After environment approval, it supplies the review summary and that
review's inline comments to Codex, commits fixes to the existing branch, and
requests re-review from the same reviewer. Checkout is pinned to the reviewed
head SHA; the push fails safely if the branch moves while approval is pending.

## Security boundaries

- A trusted reviewer must approve the `Codex-Automation` environment deployment.
- `auth.json` exists only in the runner's temporary Codex home and is deleted.
- The GitHub token is not passed to Codex.
- Issue and review text are treated as untrusted prompt content.
- Codex cannot merge, approve, or directly modify GitHub state.
- Generated changes still require normal CI, Codex review, and human approval.
- Artifacts contain only Codex's final message and expire after seven days.

Repository instructions and source code remain visible to Codex and can contain
prompt injection. The environment approval is the accepted trust boundary for
using cached Codex authentication. Keep that approval enabled, restrict who can
approve it, inspect generated diffs, and rotate `CODEX_AUTH_JSON` if output,
artifacts, commits, or logs ever contain credential material.

# Codex pull-request review

`.github/workflows/pr-code-review.yml` invokes the reusable review workflow in
`b-lens/code-kit` for pull requests targeting `main`. The repository owns the
trigger, permissions, authentication bindings, and review policy; `code-kit`
owns the runner implementation.

## Repository configuration

- `CODEX_AUTH_JSON` supplies the Codex CLI `auth.json` content. Store it only as
  an Actions repository secret and rotate it after any suspected exposure.
- `.github/codex/prompt-review.md` defines Orbit's production-safety policy and
  is loaded from the pull request's base commit.
- The wrapper grants read-only repository contents and permission to write pull
  request comments.

## Behavior

- Draft pull requests are skipped until they become ready for review.
- A new commit cancels an obsolete run and starts a review of the new head.
- Codex runs in an ephemeral, read-only sandbox and returns schema-constrained
  findings. Any finding fails the review; an empty findings list passes.
- Authentication, execution, validation, and malformed-output failures fail the
  check rather than producing a pass.
- Review diagnostics are retained as short-lived Actions artifacts.

The reusable workflow reference currently tracks `code-kit`'s `main` branch, so
its pinned tool versions and runner steps may change without an Orbit commit.
Inspect the referenced workflow when diagnosing implementation details instead
of duplicating them here.

## Fork limitation

GitHub does not provide repository secrets to workflows triggered from forks.
Fork pull requests therefore need a trusted maintainer branch or another approved
review path. Do not execute untrusted pull request code through
`pull_request_target` with repository secrets.

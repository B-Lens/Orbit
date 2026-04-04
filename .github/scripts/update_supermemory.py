import os
import subprocess
import logging

# pylint: disable=import-error
from ai_client import GitHubModelClient
from supermemory import Supermemory


# ---------------- LOGGING ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger("supermemory-update")


def run_git_command(cmd):
    """Run git command safely and raise on failure."""
    log.info("Running git command: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    log.info("Git stdout:\n%s", result.stdout)
    log.info("Git stderr:\n%s", result.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: {' '.join(cmd)}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    return result.stdout


def get_changed_files(base_sha, head_sha):
    log.info("Getting changed files between %s -> %s", base_sha, head_sha)

    if not base_sha or not head_sha:
        raise RuntimeError(
            f"Invalid SHAs: base='{base_sha}' head='{head_sha}'"
        )

    output = run_git_command(
        ["git", "diff", "--name-only", base_sha, head_sha]
    )

    files = [f for f in output.splitlines() if f.endswith(".py")]

    log.info("Changed python files: %s", files)

    return files


def get_file_diff(base_sha, head_sha, file):
    log.info("Getting diff for file: %s", file)

    output = run_git_command(
        ["git", "diff", base_sha, head_sha, "--", file]
    )

    trimmed = output[:12000]

    log.info("Diff size for %s: %s chars", file, len(trimmed))

    return trimmed


def main():
    log.info("Starting Supermemory update")

    base = os.environ.get("BASE_SHA")
    head = os.environ.get("HEAD_SHA")

    log.info("BASE_SHA: %s", base)
    log.info("HEAD_SHA: %s", head)

    # Fail hard if SHAs missing
    if not base or not head:
        raise RuntimeError(
            f"Missing SHAs: BASE_SHA={base}, HEAD_SHA={head}"
        )

    log.info("Initializing AI client")
    ai = GitHubModelClient()

    files = get_changed_files(base, head)

    if not files:
        raise RuntimeError(
            "No Python files changed — refusing to write empty memory (false positive protection)"
        )

    reasoning_output = ""

    for file in files:
        log.info("Processing file: %s", file)

        diff = get_file_diff(base, head, file)

        if not diff.strip():
            log.warning("Empty diff for file: %s", file)
            continue

        log.info("Sending diff to AI for reasoning: %s", file)

        user_prompt = f"""
            Analyze the merged PR diff and extract ONLY meaningful repository changes.
            
            Include:
            - new capability
            - behavior change
            - architecture change
            - workflow change
            - integration added/removed
            - bug fix affecting logic
            
            Exclude:
            - formatting
            - refactoring with no behavior change
            - comments/docs
            - renames
            
            Return:
            - concise bullet points
            - one change per bullet
            - no explanations
            
            FILE:
            {file}
            
            DIFF:
            {diff}
            """

        response = ai.chat(
            system_prompt="You summarize repository evolution for long-term AI memory.",
            user_prompt=user_prompt,
        )

        log.info("AI response for %s:\n%s", file, response)

        if not response.strip():
            raise RuntimeError(
                f"Empty reasoning generated for {file}"
            )

        reasoning_output += f"\n{file}:\n{response}\n"

    if not reasoning_output.strip():
        raise RuntimeError(
            "No reasoning generated — refusing to update memory"
        )

    log.info("Final reasoning output:\n%s", reasoning_output)

    # ---------- SUPERMEMORY ----------
    api_key = os.environ.get("SUPERMEMORY_API_KEY")

    if not api_key:
        raise RuntimeError(
            "SUPERMEMORY_API_KEY not set — refusing silent pass"
        )

    log.info("Initializing Supermemory client")

    memory = Supermemory(
        api_key=api_key,
    )

    log.info("Writing to Supermemory")

    memory.memories.add(
        content=reasoning_output,
        metadata={
            "type": "pr_merge",
            "base": base,
            "head": head,
        }
    )

    log.info("Supermemory updated successfully")


if __name__ == "__main__":
    main()

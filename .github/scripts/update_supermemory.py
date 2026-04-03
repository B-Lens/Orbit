import os
import subprocess
# pylint: disable=import-error
from ai_client import GitHubModelClient
from supermemory import Supermemory


def run_git_command(cmd):
    """Run git command safely and raise on failure."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: {' '.join(cmd)}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    return result.stdout


def get_changed_files(base_sha, head_sha):
    if not base_sha or not head_sha:
        raise RuntimeError(
            f"Invalid SHAs: base='{base_sha}' head='{head_sha}'"
        )

    output = run_git_command(
        ["git", "diff", "--name-only", base_sha, head_sha]
    )

    return [f for f in output.splitlines() if f.endswith(".py")]


def get_file_diff(base_sha, head_sha, file):
    output = run_git_command(
        ["git", "diff", base_sha, head_sha, "--", file]
    )

    return output[:12000]


def main():
    base = os.environ.get("BASE_SHA")
    head = os.environ.get("HEAD_SHA")

    # Fail hard if SHAs missing
    if not base or not head:
        raise RuntimeError(
            f"Missing SHAs: BASE_SHA={base}, HEAD_SHA={head}"
        )

    ai = GitHubModelClient()

    files = get_changed_files(base, head)

    if not files:
        raise RuntimeError(
            "No Python files changed — refusing to write empty memory (false positive protection)"
        )

    reasoning_output = ""

    for file in files:
        diff = get_file_diff(base, head, file)

        if not diff.strip():
            continue

        user_prompt = f"""
            You are analyzing a merged PR for Adding to the Memory Context.
            
            Extract REASONING about what changed in the repository.
            
            Focus on:
            - capability added or changed
            - behavior change
            - architectural impact
            - workflow changes
            - new integrations
            - bug fixes
            
            Do NOT mention formatting or style.
            
            Return concise bullet points.
            
            FILE:
            {file}
            
            DIFF:
            {diff}
            """

        response = ai.chat(
            system_prompt="You summarize repository evolution for long-term AI memory.",
            user_prompt=user_prompt,
        )

        if not response.strip():
            raise RuntimeError(
                f"Empty reasoning generated for {file}"
            )

        reasoning_output += f"\n{file}:\n{response}\n"

    if not reasoning_output.strip():
        raise RuntimeError(
            "No reasoning generated — refusing to update memory"
        )

    # ---------- SUPERMEMORY ----------
    api_key = os.environ.get("SUPERMEMORY_API_KEY")

    if not api_key:
        raise RuntimeError(
            "SUPERMEMORY_API_KEY not set — refusing silent pass"
        )

    memory = Supermemory(
        api_key=api_key,
    )

    memory.memories.add(
        content=reasoning_output,
        metadata={
            "type": "pr_merge",
            "base": base,
            "head": head,
        }
    )

    print("Supermemory updated successfully")


if __name__ == "__main__":
    main()

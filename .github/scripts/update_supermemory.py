import os
import subprocess
# pylint: disable=import-error
from ai_client import GitHubModelClient
from supermemory import Memory


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
    base = os.environ["BASE_SHA"]
    head = os.environ["HEAD_SHA"]

    ai = GitHubModelClient()

    try:
        files = get_changed_files(base, head)
    except Exception as e:
        print(f"Failed to compute changed files: {e}")
        return

    if not files:
        reasoning_output = "No Python logic changes."
    else:
        reasoning_output = ""

        for file in files:
            try:
                diff = get_file_diff(base, head, file)
            except Exception as e:
                print(f"Skipping {file}, git diff failed: {e}")
                continue

            if not diff.strip():
                continue

            user_prompt = f"""
                You are analyzing a merged PR.
                
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

            reasoning_output += f"\n{file}:\n{response}\n"

    # ---------- SUPERMEMORY ----------
    api_key = os.environ.get("SUPERMEMORY_API_KEY")

    if not api_key:
        print("SUPERMEMORY_API_KEY not set — skipping memory update")
        print(reasoning_output)
        return

    try:
        memory = Memory(
            api_key=api_key,
            namespace="repo-evolution"
        )

        memory.add(
            content=reasoning_output,
            metadata={
                "type": "pr_merge",
                "base": base,
                "head": head,
            }
        )

        print("Supermemory updated")

    except Exception as e:
        print(f"Supermemory update failed: {e}")


if __name__ == "__main__":
    main()

import os
import subprocess
# pylint: disable=import-error
from ai_client import GitHubModelClient
from supermemory import Memory


def get_changed_files(base_sha, head_sha):
    result = subprocess.run(
        ["git", "diff", "--name-only", base_sha, head_sha],
        capture_output=True,
        text=True,
    )

    return [f for f in result.stdout.splitlines() if f.endswith(".py")]


def get_file_diff(base_sha, head_sha, file):
    result = subprocess.run(
        ["git", "diff", base_sha, head_sha, "--", file],
        capture_output=True,
        text=True,
    )

    return result.stdout[:12000]


def main():
    base = os.environ["BASE_SHA"]
    head = os.environ["HEAD_SHA"]

    ai = GitHubModelClient()

    files = get_changed_files(base, head)

    if not files:
        reasoning_output = "No Python logic changes."
    else:
        reasoning_output = ""

        for file in files:
            diff = get_file_diff(base, head, file)

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


if __name__ == "__main__":
    main()

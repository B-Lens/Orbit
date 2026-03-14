import os
import subprocess
# pylint: disable=import-error
from ai_client import GitHubModelClient


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
        output = "No Python files changed."
    else:
        output = ""

        for file in files:
            diff = get_file_diff(base, head, file)

            if not diff.strip():
                continue

            user_prompt = f"""
                          Review the following code diff.
                          
                          Focus ONLY on:
                          - logical bugs
                          - correctness issues
                          - safety issues
                          
                          Ignore style, formatting, naming.
                          
                          If no critical issues exist, say so.
                          
                          FILE: {file}
                          
                          DIFF:
                          {diff}
                          """

            response = ai.chat(
                system_prompt="You are a strict senior software engineer performing a code review.",
                user_prompt=user_prompt,
            )

            output += f"\n\n### 📄 {file}\n{response}\n"

    github_env = os.environ["GITHUB_ENV"]

    with open(github_env, "a") as f:
        f.write("LLM_OUTPUT<<EOF\n")
        f.write(output + "\n")
        f.write("EOF\n")


if __name__ == "__main__":
    main()

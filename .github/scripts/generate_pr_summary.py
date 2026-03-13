import os
import subprocess
# pylint: disable=import-error  
from ai_client import GitHubModelClient 


def get_git_diff():
    result = subprocess.run(
        ["git", "diff", "origin/main"],
        capture_output=True,
        text=True,
    )
    return result.stdout[:12000]


def parse_summary(summary: str):
    title = "AI Autonomous Changes"
    body = ""

    for line in summary.splitlines():
        if line.startswith("TITLE:"):
            title = line.replace("TITLE:", "").strip()

        if line.startswith("BODY:"):
            body = line.replace("BODY:", "").strip()

    return title, body


def main():
    diff = get_git_diff()

    ai = GitHubModelClient()

    user_prompt = f"""
                    You are summarizing a git diff for a GitHub pull request.
                    
                    Analyze the diff and produce a concise PR title and a PR body that follows the structure below.
                    
                    Return format exactly:
                    
                    TITLE: <title>
                    
                    BODY:
                    ## Summary
                    Short explanation of what this PR does.
                    
                    ## Changes
                    - bullet point describing key change
                    - bullet point describing key change
                    
                    ## Related Issue
                    Fixes #<issue_number>
                    
                    Git diff:
                    {diff}
                    """

    summary = ai.chat(
        system_prompt="Summarize git diffs into a concise PR title and description.",
        user_prompt=user_prompt)

    title, body = parse_summary(summary)

    issue_number = os.environ.get("ISSUE_NUMBER")

    if issue_number:
        body += f"\n\nFixes #{issue_number}"

    github_env = os.environ["GITHUB_ENV"]

    with open(github_env, "a") as f:
        f.write(f"PR_TITLE={title}\n")
        f.write("PR_BODY<<EOF\n")
        f.write(body + "\n")
        f.write("EOF\n")


if __name__ == "__main__":
    main()

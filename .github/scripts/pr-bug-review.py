import os
import subprocess
import requests
# pylint: disable=import-error
from ai_client import GitHubModelClient


def run_git(cmd):
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False
    ).stdout


def get_changed_files(base_sha, head_sha):
    output = run_git(
        ["git", "diff", "--name-only", base_sha, head_sha]
    )

    return [f for f in output.splitlines() if f.endswith(".py")]


def get_file_diff(base_sha, head_sha, file):
    return run_git(
        ["git", "diff", base_sha, head_sha, "--", file]
    )[:12000]


def is_real_bug(response: str) -> bool:
    """
    Strict false-positive filter
    """

    if not response:
        return False

    text = response.strip().upper()

    if "NO CRITICAL ISSUES" in text:
        return False

    # must contain bullet
    if "-" not in response and "•" not in response:
        return False

    # avoid unsure language
    unsure_words = [
        "might",
        "possibly",
        "could",
        "maybe",
        "unclear",
        "appears",
        "seems",
        "likely",
    ]

    for w in unsure_words:
        if w in text.lower():
            return False

    return True


def create_issue(title, body):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]

    url = f"https://api.github.com/repos/{repo}/issues"

    requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={
            "title": title,
            "body": body,
            "labels": ["ai-bug"]
        },
        timeout=20
    )


def main():
    base = os.environ["BASE_SHA"]
    head = os.environ["HEAD_SHA"]
    branch = os.environ["PR_BRANCH"]
    pr_number = os.environ["PR_NUMBER"]

    ai = GitHubModelClient()

    files = get_changed_files(base, head)

    all_output = ""
    issue_body = ""

    for file in files:
        diff = get_file_diff(base, head, file)

        if not diff.strip():
            continue

        user_prompt = f"""
            You are reviewing a Git diff for correctness bugs only.
            
            Flag ONLY:
            - real logical bugs
            - crash risks
            - None access
            - incorrect conditions
            - broken state handling
            - data corruption
            - security issues
            
            Ignore:
            - style
            - refactor
            - naming
            - formatting
            - performance
            - suggestions
            
            Rules:
            - Only high confidence
            - If not 100% sure output exactly: NO CRITICAL ISSUES
            - One bullet per bug
            - include reason + impact
            
            FILE: {file}
            
            DIFF:
            {diff}
            """

        response = ai.chat(
            system_prompt="You are a strict senior engineer. Only report real bugs.",
            user_prompt=user_prompt,
        )

        all_output += f"\n\n### 📄 {file}\n{response}\n"

        if is_real_bug(response):
            issue_body += f"\n### {file}\n{response}\n"

    # write PR comment output
    github_env = os.environ["GITHUB_ENV"]

    with open(github_env, "a") as f:
        f.write("LLM_OUTPUT<<EOF\n")
        f.write(all_output + "\n")
        f.write("EOF\n")

    # create issue only if real bug
    if issue_body.strip():

        body = f"""
            AI detected correctness bug from PR #{pr_number}
            
            branch: {branch}
            
            IMPORTANT:
            Create fix branch from:
            {branch}
            
            Example:
            git checkout {branch}
            git checkout -b ai-fix-<issue>
            
            ---
            
            {issue_body}
            """

        create_issue(
            title=f"AI Bug: PR #{pr_number}",
            body=body
        )


if __name__ == "__main__":
    main()

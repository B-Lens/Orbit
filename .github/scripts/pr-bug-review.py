import os
import subprocess
import requests
import logging
# pylint: disable=import-error
from ai_client import GitHubModelClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)


def run_git(cmd):
    logging.info("Running git command: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        logging.error("Git command failed")
        logging.error("stdout: %s", result.stdout)
        logging.error("stderr: %s", result.stderr)

    return result.stdout


def get_changed_files(base_sha, head_sha):
    logging.info("Getting changed files")
    logging.info("BASE: %s", base_sha)
    logging.info("HEAD: %s", head_sha)

    output = run_git(
        ["git", "diff", "--name-only", base_sha, head_sha]
    )

    files = [f for f in output.splitlines() if f.endswith(".py")]

    logging.info("Changed python files: %s", files)

    return files


def get_file_diff(base_sha, head_sha, file):
    logging.info("Getting diff for file: %s", file)

    diff = run_git(
        ["git", "diff", base_sha, head_sha, "--", file]
    )[:12000]

    logging.info("Diff size: %s chars", len(diff))

    return diff


def is_real_bug(response: str) -> bool:
    """
    Strict false-positive filter
    """

    if not response:
        logging.info("Empty response from AI")
        return False

    text = response.strip().upper()

    if "NO CRITICAL ISSUES" in text:
        logging.info("AI reported no critical issues")
        return False

    # must contain bullet
    if "-" not in response and "•" not in response:
        logging.info("No bullet found — ignoring")
        return False

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
            logging.info("Unsure language detected — ignoring")
            return False

    logging.info("Real bug detected")
    return True


def create_issue(title, body):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]

    logging.info("Creating GitHub issue")
    logging.info("Repo: %s", repo)

    url = f"https://api.github.com/repos/{repo}/issues"

    response = requests.post(
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

    logging.info("Issue creation status: %s", response.status_code)

    if response.status_code >= 300:
        logging.error("Issue creation failed")
        logging.error(response.text)


def main():
    base = os.environ["BASE_SHA"]
    head = os.environ["HEAD_SHA"]
    branch = os.environ["PR_BRANCH"]
    pr_number = os.environ["PR_NUMBER"]

    logging.info("Starting AI bug review")
    logging.info("PR: #%s", pr_number)
    logging.info("Branch: %s", branch)
    logging.info("Base SHA: %s", base)
    logging.info("Head SHA: %s", head)

    ai = FallbackClient()

    files = get_changed_files(base, head)

    if not files:
        logging.info("No Python files changed")
        return

    all_output = ""
    issue_body = ""

    for file in files:
        diff = get_file_diff(base, head, file)

        if not diff.strip():
            logging.info("Empty diff for file: %s", file)
            continue

        logging.info("Sending file to AI: %s", file)

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

        logging.info("AI response received for %s", file)

        all_output += f"\n\n### 📄 {file}\n{response}\n"

        if is_real_bug(response):
            logging.info("Bug added from file: %s", file)
            issue_body += f"\n### {file}\n{response}\n"

    # write PR comment output
    github_env = os.environ["GITHUB_ENV"]

    logging.info("Writing output to GitHub ENV")

    with open(github_env, "a") as f:
        f.write("LLM_OUTPUT<<EOF\n")
        f.write(all_output + "\n")
        f.write("EOF\n")

    # create issue only if real bug
    if issue_body.strip():
        logging.info("Creating bug issue")

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
    else:
        logging.info("No real bugs detected")


if __name__ == "__main__":
    main()

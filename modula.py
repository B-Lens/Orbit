import os
import subprocess
import re

import os
import tiktoken
enc = tiktoken.encoding_for_model("gpt-4o")
from azure.ai.inference import ChatCompletionsClient
from azure.ai.inference.models import SystemMessage, UserMessage
from azure.core.credentials import AzureKeyCredential

from dotenv import load_dotenv
load_dotenv()



GITHUB_MODEL_TOKEN = os.getenv("GITHUB_MODEL_TOKEN")
endpoint = "https://models.github.ai/inference"
model = "openai/gpt-4.1-mini"


# -----------------------------
# 1️⃣ Get issue data
# -----------------------------
# repo = os.environ["GITHUB_REPOSITORY"]
# event_path = os.environ["GITHUB_EVENT_PATH"]
# github_token = os.environ["GITHUB_TOKEN"]

# with open(event_path) as f:
#     event = json.load(f)

# issue_number = event["issue"]["number"]
# issue_body = event["issue"]["body"]
issue_number = "01"
issue_body = """
Generate a high-level architecture and code flow documentation for the entire Python codebase.

Focus only on main logic and module interactions. Ignore minor utilities and trivial details.

Output ONLY valid Markdown structured as:

# Project Architecture Overview

## High-Level Architecture Diagram
(Use a Mermaid flowchart)

## Module Responsibilities

## Data Flow

## Entry Points

## External Dependencies

The content must be ready to save directly as docs/architecture.md.
"""

import ast

import ast

def summarize_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())

    summary = {
        "file": filepath,
        "imports": [],
        "classes": [],
        "functions": [],
    }

    for node in tree.body:

        # Imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                summary["imports"].append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            summary["imports"].append(node.module)

        # Classes
        elif isinstance(node, ast.ClassDef):
            class_info = {
                "name": node.name,
                "docstring": ast.get_docstring(node),
                "methods": []
            }

            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    class_info["methods"].append({
                        "name": item.name,
                        "docstring": ast.get_docstring(item)
                    })

            summary["classes"].append(class_info)

        # Top-level functions only
        elif isinstance(node, ast.FunctionDef):
            summary["functions"].append({
                "name": node.name,
                "docstring": ast.get_docstring(node)
            })

    return summary

# -----------------------------
# 2️⃣ Recursively collect ALL .py files
# -----------------------------
def collect_files():
    files = []
    for root, dirs, filenames in os.walk("."):
        # Skip unwanted directories
        dirs[:] = [
            d for d in dirs
            if d not in [".git", "__pycache__", "venv", ".venv", "node_modules"]
        ]

        for filename in filenames:
            if filename.endswith(".py"):
                files.append(os.path.join(root, filename))

    return files


files = collect_files()

file_contents = ""
for f in files:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            if "test_ai" in f:
                continue  # Skip this file to avoid self-reference
            print(f"Reading {f}...")
            summary = summarize_file(f)
            print(f"Summary for {f}: {summary}")
            file_contents += f"\n### FILE: {f}\n"
            file_contents += summary.__str__()  # Convert summary dict to string
    except Exception:
        continue  # Skip unreadable files safely
# exit(1)


# -----------------------------
# 3️⃣ Prompt GitHub Models
# -----------------------------
prompt = f"""
Issue:
{issue_body}

Repository files:
{file_contents}

Return ONLY a valid unified git diff.
Do not include explanations.
"""

print(f"{len(enc.encode(prompt))} tokens in prompt.")


client = ChatCompletionsClient(
    endpoint=endpoint,
    credential=AzureKeyCredential(GITHUB_MODEL_TOKEN),
)

response = client.complete(
    messages=[
        SystemMessage("You are an AI developer."),
        UserMessage(prompt),
    ],
    temperature=1.0,
    max_tokens=3000,
    top_p=1.0,
    model=model
)

diff = response.choices[0].message.content.strip()


# -----------------------------
# 4️⃣ Clean Diff (IMPORTANT)
# -----------------------------
def clean_diff(raw_diff: str) -> str:
    # Remove ```diff or ``` wrappers
    raw_diff = re.sub(r"^```.*?\n", "", raw_diff)
    raw_diff = re.sub(r"\n```$", "", raw_diff)
    return raw_diff.strip()

diff = clean_diff(diff)

# Validate format
if not diff.startswith("diff --git"):
    print("❌ Invalid diff format returned by model.")
    print("---- MODEL OUTPUT START ----")
    print(diff[:500])
    print("---- MODEL OUTPUT END ----")
    exit(1)


# -----------------------------
# 5️⃣ Save patch
# -----------------------------
with open("patch.diff", "w") as f:
    f.write(diff)


# -----------------------------
# 6️⃣ Apply patch
# -----------------------------
result = subprocess.run(
    ["git", "apply", "--reject", "--whitespace=fix", "patch.diff"]
)

if result.returncode != 0:
    print("❌ Patch failed to apply.")
    exit(1)


# -----------------------------
# 8️⃣ Commit and push
# -----------------------------
branch = f"ai-fix-{issue_number}"
subprocess.run(["git", "checkout", "-b", branch])
subprocess.run(["git", "add", "."])
subprocess.run(["git", "commit", "-m", f"AI Fix for issue #{issue_number}"])
subprocess.run(["git", "push", "origin", branch])
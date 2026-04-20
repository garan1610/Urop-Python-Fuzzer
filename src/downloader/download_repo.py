import git
import os
import json
from pathlib import Path
import shutil
import argparse
import logging


logger = logging.getLogger('download_repo')


REPOS_DIR = Path(__file__).parent / "repos"

# Run python download_repo.py --project luigi
# Git URL and commit ID for each project
with open(REPOS_DIR / "typebugs_repo.json", "r") as f:
    typebugs_repo = json.load(f)
with open(REPOS_DIR / "bugsinpy_repo.json", "r") as f:
    bugsinpy_repo = json.load(f)
with open(REPOS_DIR / "excepy_repo.json", "r") as f:
    excepy_repo = json.load(f)

# Directory to clone repositories into
DOWNLOADS_DIR = Path("downloads")


def clone_and_checkout(project, info):
    repo_url = info['git_url']
    commit_id = info['commit_id']

    # repository name and directory
    repo_name = repo_url.split("/")[-1].replace(".git", "")
    repo_dir = DOWNLOADS_DIR / repo_name

    # Clone the repository if it doesn't exist
    if not os.path.exists(repo_dir):
        logger.info(f"Cloning {repo_name}...")
        repo = git.Repo.clone_from(repo_url, repo_dir)
    else:
        logger.info(f"Repository {repo_name} already exists. Skipping clone.")
        repo = git.Repo(repo_dir)

    # Copy the repository to the project name
    new_repo_dir = DOWNLOADS_DIR / project
    if not os.path.exists(new_repo_dir):
        logger.info(f"Copying {repo_name} to {project}...")
        shutil.copytree(repo_dir, new_repo_dir)
    else:
        logger.info(f"Project {project} already exists. Skipping copy.")

    # Checkout the commit
    logger.info(f"Checking out commit {commit_id}...")
    repo = git.Repo(new_repo_dir)
    repo.git.checkout(commit_id, force=True)


def download_repo(project: str):
    if project in typebugs_repo:
        clone_and_checkout(project, typebugs_repo[project])
    elif project in bugsinpy_repo:
        clone_and_checkout(project, bugsinpy_repo[project])
    elif project in excepy_repo:
        clone_and_checkout(project, excepy_repo[project])
    else:
        logger.info(f"Project {project} not found in any repository list.")


def download_all(project_filter: str | None = None):
    logger.info("Downloading all projects...")
    target = project_filter

    for project, info in typebugs_repo.items():
        if target and target not in project:
            continue
        clone_and_checkout(project, info)
    for project, info in bugsinpy_repo.items():
        if target and target not in project:
            continue
        clone_and_checkout(project, info)
    for project, info in excepy_repo.items():
        if target and target not in project:
            continue
        clone_and_checkout(project, info)


def list_projects():
    print("Available projects to download:")
    for project in typebugs_repo.keys():
        print(f"{project} (TypeBugs)")
    for project in bugsinpy_repo.keys():
        print(f"{project} (BugsInPy)")
    for project in excepy_repo.keys():
        print(f"{project} (ExcePy)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=str, default=None)
    args = ap.parse_args()
    download_all(args.project)

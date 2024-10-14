from git import Repo


def git_clone_iohk_repo(repo_name, repo_dir, repo_branch):
    repo = Repo.clone_from(f"https://github.com/input-output-hk/{repo_name}.git", repo_dir)
    repo.git.checkout(repo_branch)
    print(f"Repo: {repo_name} cloned to: {repo_dir}")
    return repo


def git_checkout(repo, rev):
    print(f"Checked out rev: {rev} of {repo}")
    repo.git.checkout(rev)
    return repo

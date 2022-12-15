from git import Repo


def git_clone_iohk_repo(repo_name, repo_dir):
    Repo.clone_from(f"https://github.com/input-output-hk/{repo_name}.git", repo_dir)
    print(f"Repo: {repo_name} cloned to: {repo_dir}")


def git_checkout_branch(repo_branch):
    Repo.git.checkout(repo_branch)

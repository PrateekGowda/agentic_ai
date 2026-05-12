from dataclasses import dataclass

from github import Github, GithubException


@dataclass(frozen=True)
class RepositoryResult:
    name: str
    url: str


class GitHubRepositoryClient:
    def __init__(self, token: str | None, owner: str | None) -> None:
        self._token = token
        self._owner = owner

    def create_repository(self, name: str, private: bool = True) -> RepositoryResult:
        if not self._token or not self._owner:
            return RepositoryResult(
                name=name,
                url=f"https://github.com/{self._owner or 'example-org'}/{name}",
            )

        github = Github(self._token)
        try:
            org = github.get_organization(self._owner)
            repo = org.create_repo(name=name, private=private, auto_init=True)
        except GithubException:
            user = github.get_user()
            repo = user.create_repo(name=name, private=private, auto_init=True)

        return RepositoryResult(name=repo.name, url=repo.html_url)

    def upsert_file(
        self,
        repo_full_name: str,
        path: str,
        content: str,
        message: str,
        branch: str = "main",
    ) -> None:
        if not self._token:
            return

        github = Github(self._token)
        repo = github.get_repo(repo_full_name)
        try:
            existing = repo.get_contents(path, ref=branch)
            if isinstance(existing, list):
                raise ValueError(f"Expected file path, got directory: {path}")
            repo.update_file(path, message, content, existing.sha, branch=branch)
        except GithubException as exc:
            if exc.status != 404:
                raise
            repo.create_file(path, message, content, branch=branch)

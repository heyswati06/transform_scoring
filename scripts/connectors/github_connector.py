"""
connectors/github_connector.py
Person 2 owns this file.

Wraps GitHub Enterprise API calls needed by hygiene_checker.py.
Handles pagination, rate limiting, and multi-org support.
All repos for all apps are discovered via the registry.

Usage:
    python scripts/connectors/github_connector.py --test
    python scripts/connectors/github_connector.py --list-repos
"""
import argparse, logging, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests, yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent.parent

def load_cfg():
    with open(ROOT / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)

class GitHubConnector:
    def __init__(self, cfg):
        self.base_url = cfg["github"]["base_url"].rstrip("/")
        self.token    = os.environ.get(cfg["github"]["token_env"], "")
        if not self.token:
            raise EnvironmentError(
                f"GitHub token not set. Run: export {cfg['github']['token_env']}=your_token"
            )
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json",
        })
        retry = Retry(total=5, backoff_factor=1, status_forcelist=[429, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://",  HTTPAdapter(max_retries=retry))

    def test(self) -> bool:
        try:
            r = self.session.get(f"{self.base_url}/user", timeout=10)
            r.raise_for_status()
            logger.info(f"GitHub connection: OK (authenticated as {r.json().get('login')})")
            return True
        except Exception as e:
            logger.error(f"GitHub connection failed: {e}")
            return False

    def _get(self, path: str, params: dict = None):
        r = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
        self._handle_rate_limit(r)
        r.raise_for_status()
        return r.json()

    def _paginate(self, path: str, params: dict = None):
        params = {**(params or {}), "per_page": 100, "page": 1}
        while True:
            r = self.session.get(f"{self.base_url}{path}", params=params, timeout=30)
            self._handle_rate_limit(r)
            r.raise_for_status()
            data = r.json()
            if not data: break
            yield from data
            if "next" not in r.links: break
            params["page"] += 1

    def _handle_rate_limit(self, r):
        if r.status_code in (429, 403) and "rate limit" in r.text.lower():
            wait = max(int(r.headers.get("X-RateLimit-Reset", time.time() + 60)) - int(time.time()), 10)
            logger.warning(f"Rate limited. Sleeping {wait}s...")
            time.sleep(wait)

    # ── Branch methods ────────────────────────────────────────
    def list_branches(self, org: str, repo: str):
        return list(self._paginate(f"/repos/{org}/{repo}/branches"))

    def get_branch_commit_date(self, org: str, repo: str, branch: str) -> datetime:
        data = self._get(f"/repos/{org}/{repo}/branches/{branch}")
        ds   = data["commit"]["commit"]["committer"]["date"]
        return datetime.fromisoformat(ds.replace("Z", "+00:00"))

    def get_branch_protection(self, org: str, repo: str, branch: str):
        try:
            return self._get(f"/repos/{org}/{repo}/branches/{branch}/protection")
        except requests.HTTPError as e:
            if e.response.status_code == 404: return None
            raise

    # ── PR methods ─────────────────────────────────────────────
    def list_open_prs(self, org: str, repo: str):
        return list(self._paginate(f"/repos/{org}/{repo}/pulls", {"state": "open"}))

    def get_pr_files(self, org: str, repo: str, pr_number: int):
        return list(self._paginate(f"/repos/{org}/{repo}/pulls/{pr_number}/files"))

    def get_pr_reviews(self, org: str, repo: str, pr_number: int):
        return list(self._paginate(f"/repos/{org}/{repo}/pulls/{pr_number}/reviews"))

    def get_pr_commits(self, org: str, repo: str, pr_number: int):
        return list(self._paginate(f"/repos/{org}/{repo}/pulls/{pr_number}/commits"))

    # ── Events ─────────────────────────────────────────────────
    def get_push_events(self, org: str, repo: str):
        events = list(self._paginate(f"/repos/{org}/{repo}/events"))
        return [e for e in events if e.get("type") == "PushEvent"]

    # ── Repo-level multi-org support ───────────────────────────
    def get_repos_for_org(self, org: str) -> list[dict]:
        return list(self._paginate(f"/orgs/{org}/repos", {"type": "all"}))

def run(args):
    cfg = load_cfg()
    connector = GitHubConnector(cfg)

    if args.test:
        ok = connector.test()
        sys.exit(0 if ok else 1)

    if args.list_repos:
        sys.path.insert(0, str(ROOT / "scripts"))
        from registry_loader import RegistryLoader
        loader = RegistryLoader(str(ROOT / "registry"))
        apps   = loader.load_all()
        repos  = loader.get_all_repos()
        print(f"\nAll repos across {len(apps)} apps ({len(repos)} total):\n")
        for org, repo, app_id, is_primary in sorted(repos):
            primary = " ★ primary" if is_primary else ""
            print(f"  {org}/{repo}  [{app_id}]{primary}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser()
    p.add_argument("--test",       action="store_true", help="Test GitHub connection")
    p.add_argument("--list-repos", action="store_true", help="List all repos from registry")
    run(p.parse_args())

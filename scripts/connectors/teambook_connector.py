"""
connectors/teambook_connector.py
Person 1 owns this file.

Pulls team/pod details from Teambook.
Used to pre-populate team_lead_email and team_id in intake files.
Writes results to reports/latest_teams.json.

Usage:
    python scripts/connectors/teambook_connector.py --test
    python scripts/connectors/teambook_connector.py
"""
import argparse, json, logging, os, sys
from datetime import datetime, timezone
from pathlib import Path
import requests, yaml

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent.parent

def load_cfg():
    with open(ROOT / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)

class TeambookConnector:
    def __init__(self, cfg):
        self.base_url   = cfg["teambook"]["base_url"].rstrip("/")
        self.token      = os.environ.get(cfg["teambook"]["token_env"], "")
        self.lead_field = cfg["teambook"].get("team_lead_field", "lead_email")
        self.session    = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"

    def test(self):
        try:
            r = self.session.get(f"{self.base_url}/api/health", timeout=10)
            r.raise_for_status()
            logger.info("Teambook connection: OK")
            return True
        except Exception as e:
            logger.error(f"Teambook connection failed: {e}")
            return False

    def get_all_teams(self) -> list[dict]:
        """Returns all teams with their apps and lead emails."""
        try:
            r = self.session.get(f"{self.base_url}/api/teams", timeout=20)
            r.raise_for_status()
            return r.json().get("teams", [])
        except Exception as e:
            logger.warning(f"Teambook team list failed: {e}")
            return []

    def get_team_for_app(self, app_name: str) -> dict:
        """Find team lead email and team ID for a given app name."""
        try:
            r = self.session.get(f"{self.base_url}/api/teams/by-app/{app_name}", timeout=10)
            r.raise_for_status()
            data = r.json()
            return {
                "teambook_team_id":  data.get("id", ""),
                "team_lead_email":   data.get(self.lead_field, ""),
                "team_lead_name":    data.get("lead_name", ""),
            }
        except Exception as e:
            logger.debug(f"Teambook lookup failed for {app_name}: {e}")
            return {}

    def build_app_to_team_map(self) -> dict[str, dict]:
        """Returns {app_name: {team_lead_email, teambook_team_id}} for all apps."""
        teams = self.get_all_teams()
        mapping = {}
        for team in teams:
            lead_email = team.get(self.lead_field, "")
            team_id    = team.get("id", "")
            for app in team.get("apps", []):
                mapping[app] = {"team_lead_email": lead_email, "teambook_team_id": team_id}
        return mapping

def run(args):
    cfg = load_cfg()
    connector = TeambookConnector(cfg)

    if args.test:
        ok = connector.test()
        sys.exit(0 if ok else 1)

    logger.info("Fetching team data from Teambook...")
    mapping = connector.build_app_to_team_map()

    out = ROOT / "reports" / "latest_teams.json"
    out.parent.mkdir(exist_ok=True)
    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "team_count": len(set(v["teambook_team_id"] for v in mapping.values())),
        "app_count": len(mapping),
        "app_to_team": mapping,
    }
    out.write_text(json.dumps(result, indent=2))
    logger.info(f"Team data saved to {out}")
    logger.info(f"Apps mapped: {len(mapping)}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true")
    run(p.parse_args())

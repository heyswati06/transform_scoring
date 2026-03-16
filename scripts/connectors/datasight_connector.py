"""
connectors/datasight_connector.py
Person 1 owns this file.

Pulls RF, LTDD, CFR, MTTR for all apps from DataSight API.
Writes results to reports/latest_metrics.json.

Usage:
    python scripts/connectors/datasight_connector.py --test     # test connection only
    python scripts/connectors/datasight_connector.py            # pull all metrics
    python scripts/connectors/datasight_connector.py --app payments-api
"""
import argparse, json, logging, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import requests, yaml

logger = logging.getLogger(__name__)
ROOT = Path(__file__).parent.parent.parent

def load_cfg():
    with open(ROOT / "config" / "settings.yaml") as f:
        return yaml.safe_load(f)

class DataSightConnector:
    def __init__(self, cfg):
        self.base_url = cfg["datasight"]["base_url"].rstrip("/")
        self.token    = os.environ.get(cfg["datasight"]["token_env"], "")
        self.fmap     = cfg["datasight"].get("field_map", {})
        self.session  = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers["Accept"] = "application/json"

    def test(self):
        """Test connectivity. Returns True if OK."""
        try:
            r = self.session.get(f"{self.base_url}/api/health", timeout=10)
            r.raise_for_status()
            logger.info("DataSight connection: OK")
            return True
        except Exception as e:
            logger.error(f"DataSight connection failed: {e}")
            # ── PLACEHOLDER: remove when real API is confirmed ──
            logger.warning("Running in STUB mode — returning placeholder data")
            return False

    def get_all_apps(self):
        """Returns list of app dicts from DataSight."""
        try:
            r = self.session.get(f"{self.base_url}/api/v1/apps", timeout=20)
            r.raise_for_status()
            return r.json().get("apps", [])
        except Exception as e:
            logger.warning(f"DataSight app list failed: {e}. Using registry as source.")
            return []

    def get_metrics(self, datasight_app_name: str, days: int = 30) -> dict:
        """
        Get RF, LTDD, CFR, MTTR for one app over the last N days.
        Returns dict with standardised keys regardless of DataSight field names.
        """
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        try:
            r = self.session.get(
                f"{self.base_url}/api/v1/apps/{datasight_app_name}/metrics",
                params={"since": since, "period": f"last_{days}_days"},
                timeout=15
            )
            r.raise_for_status()
            raw = r.json()

            # Map DataSight field names → our standard names
            return {
                "datasight_app_name": datasight_app_name,
                "period_days":        days,
                "rf":   raw.get(self.fmap.get("release_frequency", "deployment_frequency")),
                "ltdd": raw.get(self.fmap.get("ltdd", "lead_time_days")),
                "cfr":  raw.get(self.fmap.get("cfr",  "change_failure_rate")),
                "mttr": raw.get(self.fmap.get("mttr", "mean_time_to_restore_hours")),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            logger.warning(f"DataSight metrics failed for {datasight_app_name}: {e}")
            # ── STUB: return None values so scoring engine knows data is missing ──
            return {
                "datasight_app_name": datasight_app_name,
                "period_days": days,
                "rf": None, "ltdd": None, "cfr": None, "mttr": None,
                "error": str(e),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

def run(args):
    cfg = load_cfg()
    connector = DataSightConnector(cfg)

    if args.test:
        ok = connector.test()
        sys.exit(0 if ok else 1)

    # Load registry to know which apps to fetch
    sys.path.insert(0, str(ROOT / "scripts"))
    from registry_loader import RegistryLoader
    loader = RegistryLoader(str(ROOT / "registry"))
    apps   = loader.load_all()

    if args.app:
        apps = [a for a in apps if args.app.lower() in a.app_id.lower()]

    logger.info(f"Fetching DataSight metrics for {len(apps)} apps...")
    results = []
    for app in apps:
        ds_name = app.datasight.get("app_name") or app.display_name
        metrics = connector.get_metrics(ds_name)
        metrics["app_id"] = app.app_id
        results.append(metrics)
        status = f"RF={metrics['rf']}, LTDD={metrics['ltdd']}" if metrics.get("rf") else "no data"
        logger.info(f"  {app.app_id}: {status}")

    out = ROOT / "reports" / "latest_metrics.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2, default=str))
    logger.info(f"\nMetrics saved to {out}")
    logger.info(f"Apps with RF data:  {sum(1 for r in results if r.get('rf'))}/{len(results)}")
    logger.info(f"Apps with LTDD data:{sum(1 for r in results if r.get('ltdd'))}/{len(results)}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true")
    p.add_argument("--app", help="Fetch one app only")
    run(p.parse_args())

"""READ-ONLY. Each trader's saved chart template (user_chart_settings): who, when it was last saved, and the chart keys that decide the GEX
profile and the heatmap (2026-11-06: IWM's profile vanishes with the heatmap on; layer windows not keeping their choices). No passwords or tokens."""
import json
import sys

sys.path.insert(0, "V:/Convexa/repo")
sys.stdout.reconfigure(encoding="utf-8")
from sqlalchemy import text

from backend.core.container import build_container

KEYS = ("profile", "heatmap", "heatmapMetric", "heatmapSides", "gammaView", "gammaTab", "heatmapDayScale", "timeframe", "smile", "flow")
c = build_container()
with c.storage.session_factory() as session:
    cols = [r[0] for r in session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user_chart_settings'"))]
    print("columns", cols)
    rows = session.execute(text(
        "SELECT s.*, u.username FROM user_chart_settings s LEFT JOIN users u ON u.id = s.user_id ORDER BY s.user_id")).mappings().all()
    for row in rows:
        settings = row.get("settings")
        if isinstance(settings, str):
            settings = json.loads(settings)
        settings = settings or {}
        print(row.get("username"), row.get("updated_at"), {k: settings.get(k) for k in KEYS}, "| other keys:", sorted(k for k in settings if k not in KEYS))

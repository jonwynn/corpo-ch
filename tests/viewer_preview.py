"""Serves deterministic viewer examples on loopback without deployment services."""

import argparse
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from tests.viewer_test_bootstrap import ViewerTestEnvironment


def render_previews(directory):
    """Renders real templates inside the guarded, database-free environment.

    :param str directory: Disposable runtime directory
    :return: In-memory pages, fragments and static assets"""
    fixtures = json.loads((Path(__file__).parent / "fixtures" / "match_viewer_cases.json").read_text(encoding="utf-8"))
    cases = {item["case_id"]: item for item in fixtures["cases"]}
    long_names = deepcopy(cases["approved_second_pick"])
    long_names["case_id"] = "long_names"
    long_names["source"]["players"][0]["name"] = "A player with a considerably longer display name"
    long_names["source"]["players"][1]["name"] = "AnotherPlayerWithAnUnbrokenNameThatNeedsToWrap"
    long_names["source"]["rounds"][-1]["chart_title"] = "An extended chart title with featured performers and an additional tournament arrangement [CORP Edit]"
    cases["long_names"] = long_names
    selected = list(cases.values())
    pages = {}
    fragments = {}
    preview_names = {"approved_opening", "approved_first_pick", "approved_second_pick", "long_names", "target_best_of_9", "missing_participant"}
    choices = [{"url": f"/?case={item['case_id']}", "label": item["case_id"].replace("_", " ")} for item in selected if item["case_id"] in preview_names]
    with ViewerTestEnvironment(directory, allow_models=True):
        import django
        from django.template.loader import render_to_string
        if sys.platform == "win32":
            with patch("platform.system", return_value="Windows"):
                django.setup()
        else:
            django.setup()
        from corpoch.match_viewer import build_match_presentation
        for case in selected:
            viewer = build_match_presentation(case["source"], case["request"]["pins"])
            context = {"viewer": viewer, "preview_mode": True, "fixture_choices": choices}
            pages[case["case_id"]] = render_to_string("match_viewer/page.html", context).encode()
            fragments[case["case_id"]] = render_to_string("match_viewer/state.html", {"viewer": viewer, "state_url": "/state"}).encode()
        # A controlled polling example exercises browser DOM updates with the
        # same three fixture states. It contains no real staff or match data.
        initial = deepcopy(cases["approved_opening"])
        viewer = build_match_presentation(initial["source"])
        page = render_to_string("match_viewer/page.html", {"viewer": viewer, "preview_mode": True, "state_url": "/state"})
        controls = '<aside class="mv-preview"><p>Fixture controls · example data only</p>'
        for name, label in [("opening", "Initial bans"), ("first", "First pick"), ("later", "Later round"), ("failure", "Simulate failure"), ("denied", "Simulate access loss")]:
            controls += f'<button type="button" data-example="{name}">{label}</button> '
        controls += '</aside><script src="/example-controls.js" defer></script>'
        pages["live_example"] = page.replace("Illustrative match preview · live updates off", "Illustrative match preview · simulated updates").replace('<main class="mv-shell">', '<main class="mv-shell">' + controls).encode()
    assets = {}
    for name in ("match_viewer.css", "match_viewer.js"):
        assets[f"/test-static/corpoch/{name}"] = (Path(__file__).parents[1] / "corpoch" / "static" / "corpoch" / name).read_bytes()
    return pages, fragments, assets


def serve_preview(port):
    """Serves only in-memory illustrative artifacts; never imports live settings.

    :param int port: Loopback port"""
    with tempfile.TemporaryDirectory(prefix="corpo-viewer-preview-") as directory:
        pages, fragments, assets = render_previews(directory)
    example = {"case": "approved_opening", "status": 200}
    names = list(pages)
    first = next(name for name in names if name.startswith("approved_") and "first" in name)
    later = next(name for name in names if name.startswith("approved_") and name not in {first, "approved_opening"})

    class PreviewHandler(BaseHTTPRequestHandler):
        """Reads fixture documents and changes only the in-memory example selector."""

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            status, content_type = 200, "text/html; charset=utf-8"
            if parsed.path == "/":
                body = pages.get(query.get("case", ["approved_opening"])[0])
            elif parsed.path == "/state":
                status = example["status"]
                body = fragments[example["case"]] if status == 200 else b"Example request failure"
            elif parsed.path == "/example":
                selection = query.get("select", ["opening"])[0]
                example["case"] = {"opening": "approved_opening", "first": first, "later": later}.get(selection, example["case"])
                example["status"] = {"failure": 503, "denied": 403}.get(selection, 200)
                body = b"Example changed"
            elif parsed.path == "/example-controls.js":
                content_type = "text/javascript; charset=utf-8"
                body = b'document.querySelectorAll("[data-example]").forEach(button => button.addEventListener("click", () => fetch("/example?select=" + button.dataset.example, {cache: "no-store"})));'
            else:
                body = assets.get(parsed.path)
                content_type = "text/css" if parsed.path.endswith(".css") else "text/javascript"
            if body is None:
                status, body = 404, b"Preview file not found"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format_string, *args):
            """Keeps normal local fixture polling out of the terminal log."""
            return

    with HTTPServer(("127.0.0.1", port), PreviewHandler) as server:
        print(f"Example viewer: http://127.0.0.1:{port}/?case=live_example", flush=True)
        print("Only illustrative fixtures are served. Ctrl+C stops the preview.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    serve_preview(parser.parse_args().port)

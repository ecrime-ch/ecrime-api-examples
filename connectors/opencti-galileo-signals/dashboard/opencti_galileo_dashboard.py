#!/usr/bin/env python3
"""Small Galileo Signals investigation dashboard for an OpenCTI instance."""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


OPENCTI_URL = os.getenv("OPENCTI_URL", "http://127.0.0.1:8080").rstrip("/")
OPENCTI_TOKEN = os.getenv("OPENCTI_ADMIN_TOKEN") or os.getenv("OPENCTI_TOKEN", "")
HOST = os.getenv("GALILEO_DASHBOARD_HOST", "127.0.0.1")
PORT = int(os.getenv("GALILEO_DASHBOARD_PORT", "8091"))
DEFAULT_QUERY = "Galileo observed domain"


def graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    if not OPENCTI_TOKEN:
        raise RuntimeError("OPENCTI_ADMIN_TOKEN or OPENCTI_TOKEN is required")
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    request = urllib.request.Request(
        f"{OPENCTI_URL}/graphql",
        data=body,
        headers={
            "Authorization": f"Bearer {OPENCTI_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.loads(response.read())
    if payload.get("errors"):
        raise RuntimeError(payload["errors"][0].get("message", "OpenCTI GraphQL error"))
    return payload["data"]


def indicator_search(term: str, first: int = 50) -> dict[str, Any]:
    query = """
    query GalileoIndicators($first: Int!, $search: String!) {
      indicators(first: $first, search: $search) {
        pageInfo { globalCount }
        edges {
          node {
            id
            name
            pattern
            confidence
            created
            updated_at
          }
        }
      }
    }
    """
    return graphql(query, {"first": first, "search": term})["indicators"]


def domain_from_pattern(pattern: str | None) -> str:
    match = re.search(r"domain-name:value\s*=\s*'([^']+)'", pattern or "")
    return match.group(1) if match else ""


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_page(search: str, result: dict[str, Any] | None, error: str | None) -> bytes:
    rows = []
    total = 0
    if result:
        total = int(result.get("pageInfo", {}).get("globalCount") or 0)
        for edge in result.get("edges", []):
            node = edge.get("node", {})
            domain = domain_from_pattern(node.get("pattern"))
            opencti_search = "/dashboard/search?search=" + urllib.parse.quote(domain or node.get("name", ""))
            rows.append(
                "<tr>"
                f"<td><a href='{esc(opencti_search)}'>{esc(domain or node.get('name'))}</a></td>"
                f"<td>{esc(node.get('confidence'))}</td>"
                f"<td><code>{esc(node.get('pattern'))}</code></td>"
                f"<td>{esc(node.get('updated_at') or node.get('created'))}</td>"
                "</tr>"
            )

    sample_terms = ["Galileo observed domain", "xzqsrc.com", "phishing", "domain-name"]
    sample_links = " ".join(
        f"<a class='chip' href='?q={urllib.parse.quote(term)}'>{esc(term)}</a>"
        for term in sample_terms
    )
    error_html = f"<div class='error'>{esc(error)}</div>" if error else ""
    table_html = "\n".join(rows) if rows else "<tr><td colspan='4' class='muted'>No matches for this query.</td></tr>"
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Galileo Signals in OpenCTI</title>
  <style>
    :root {{ color-scheme: light dark; --bg:#0f172a; --panel:#111827; --line:#263244; --fg:#e5e7eb; --muted:#9ca3af; --accent:#38bdf8; }}
    body {{ margin:0; font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--fg); }}
    main {{ max-width:1180px; margin:0 auto; padding:28px; }}
    h1 {{ margin:0 0 8px; font-size:28px; }}
    h2 {{ margin:0 0 10px; font-size:16px; }}
    .muted {{ color:var(--muted); }}
    .grid {{ display:grid; grid-template-columns:1.2fr .8fr; gap:16px; margin:20px 0; }}
    .panel {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
    form {{ display:flex; gap:8px; margin:14px 0; }}
    input {{ flex:1; padding:10px 12px; border-radius:6px; border:1px solid var(--line); background:#020617; color:var(--fg); }}
    button, .chip {{ padding:9px 12px; border-radius:6px; border:1px solid var(--line); background:#172033; color:var(--fg); text-decoration:none; }}
    button {{ cursor:pointer; }}
    .chip {{ display:inline-block; margin:4px 4px 0 0; }}
    .stat {{ font-size:30px; font-weight:700; }}
    table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
    th, td {{ text-align:left; border-bottom:1px solid var(--line); padding:10px 8px; vertical-align:top; }}
    th {{ color:#cbd5e1; font-size:12px; text-transform:uppercase; }}
    code {{ color:#bae6fd; white-space:normal; word-break:break-word; }}
    a {{ color:var(--accent); }}
    .error {{ border:1px solid #7f1d1d; background:#3f1212; color:#fecaca; padding:10px; border-radius:6px; margin:12px 0; }}
    ul {{ margin:8px 0 0; padding-left:18px; }}
    @media (max-width: 820px) {{ main {{ padding:18px; }} .grid {{ grid-template-columns:1fr; }} form {{ flex-direction:column; }} }}
  </style>
</head>
<body>
<main>
  <h1>Galileo Signals in OpenCTI</h1>
  <p class="muted">Investigate the Galileo observed-domain feed imported into this OpenCTI test instance.</p>
  <div class="grid">
    <section class="panel">
      <h2>Search Imported Indicators</h2>
      <form method="get">
        <input name="q" value="{esc(search)}" placeholder="Search a domain, e.g. xzqsrc.com">
        <button type="submit">Search</button>
      </form>
      <div>{sample_links}</div>
      {error_html}
    </section>
    <section class="panel">
      <h2>What Is Available Now</h2>
      <div class="stat">{total:,}</div>
      <div class="muted">matching Galileo indicators</div>
      <ul>
        <li>Search domains imported from Galileo's observed-domain feed.</li>
        <li>Each row is an OpenCTI Indicator with a STIX domain-name pattern.</li>
        <li>Confidence maps from Galileo confidence into OpenCTI confidence.</li>
      </ul>
    </section>
  </div>
  <section class="panel">
    <h2>What You Can Search For</h2>
    <ul>
      <li><strong>Domains:</strong> yes, for example <code>xzqsrc.com</code> or <code>docomoservice.picshareit.com</code>.</li>
      <li><strong>Full links/URLs:</strong> not in this connector yet; the current feed imports domains, not complete URLs.</li>
      <li><strong>Email addresses:</strong> not in this connector yet; sender domains may be present, raw sender addresses are not.</li>
      <li><strong>Screenshots/raw emails:</strong> not shown or imported. OpenCTI receives indicators only, no Galileo screenshots or EML evidence.</li>
    </ul>
  </section>
  <section class="panel" style="margin-top:16px">
    <h2>Results</h2>
    <table>
      <thead><tr><th>Domain</th><th>Confidence</th><th>STIX Pattern</th><th>Updated</th></tr></thead>
      <tbody>{table_html}</tbody>
    </table>
  </section>
</main>
</body>
</html>"""
    return page.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/", "/galileo-dashboard", "/galileo-dashboard/"):
            self.send_error(404)
            return
        params = urllib.parse.parse_qs(parsed.query)
        search = (params.get("q", [DEFAULT_QUERY])[0] or DEFAULT_QUERY).strip()
        result = None
        error = None
        try:
            result = indicator_search(search)
        except (RuntimeError, urllib.error.URLError, TimeoutError) as exc:
            error = str(exc)
        body = render_page(search, result, error)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(fmt % args, file=sys.stderr)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Galileo dashboard listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

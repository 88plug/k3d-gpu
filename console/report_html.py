#!/usr/bin/env python3
"""report.html: one self-contained page rendered from facts/v1 and plan/v1.

  report_html.py [--matrix FILE] < facts.json   report.html on stdout

render(facts, plan) is a renderer, not a decision: it prints the plan's own
summary lines and the facts as they are, and computes nothing but layout and the
sha256 of the facts it shows. The page runs no script and loads nothing: every
style is inline and allowed by hash in its Content-Security-Policy
(default-src 'none'), and the only URLs in it are deep links (<a href>) for a
reader to follow. No clock: the same documents give the same bytes.

Python standard library only.
"""

import argparse
import base64
import hashlib
import html
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import facts as factsmod  # noqa: E402  (stdlib-only sibling modules)
import plan as planmod  # noqa: E402
import schema_check  # noqa: E402

DOCS = "https://github.com/88plug/k3d-gpu"
URL_RE = re.compile(r"https?://[^\s<>\"'`]+")
CODE_RE = re.compile(r"`([^`]+)`")
LEVELS = factsmod.LEVELS + ("other",)
DETAIL_KEYS = ("from", "why", "apply", "revert")

STYLE = """
:root{--bg:#fbfbf9;--fg:#1d1d1b;--mut:#6a6a64;--line:#deded7;--panel:#f2f2ed;--code:#ecece6;
--green:#1d7a3e;--green-bg:#e3f3e8;--amber:#8a5a00;--amber-bg:#fbefd6;--red:#a52a2a;--red-bg:#f8e3e1;
--blue:#2a5a9a;--blue-bg:#e4ecf7;--grey:#5f5f59;--grey-bg:#ecece6;--link:#2a5a9a}
@media (prefers-color-scheme:dark){:root{--bg:#151514;--fg:#e9e9e3;--mut:#9d9d95;--line:#34342f;
--panel:#1d1d1b;--code:#272724;--green:#6cd08e;--green-bg:#17301f;--amber:#e8b45a;--amber-bg:#33280f;
--red:#ef8a80;--red-bg:#3a1c19;--blue:#8db4ea;--blue-bg:#1a2638;--grey:#b0b0a8;--grey-bg:#2a2a27;--link:#8db4ea}}
*{box-sizing:border-box}
body{margin:0;padding:28px 16px 40px;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1040px;margin:0 auto}
header{border-bottom:2px solid var(--fg);padding-bottom:12px;margin-bottom:20px}
h1{font-size:22px;margin:0;letter-spacing:-.01em}
.sub{color:var(--mut);margin:4px 0 0;overflow-wrap:anywhere}
h2{font-size:12px;margin:30px 0 10px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut)}
h3{font-size:14px;margin:18px 0 6px}
.verdict{border:1px solid var(--line);border-left:6px solid var(--grey);background:var(--panel);padding:14px 16px;border-radius:4px}
.verdict.green{border-left-color:var(--green)}.verdict.unmeasured{border-left-color:var(--amber)}
.verdict ul{margin:8px 0 0;padding-left:18px}
code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--code);padding:1px 4px;border-radius:3px;overflow-wrap:anywhere}
a{color:var(--link);overflow-wrap:anywhere}
.wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:4px 0}
th,td{border:1px solid var(--line);padding:5px 8px;vertical-align:top;text-align:left}
thead th{background:var(--panel);font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
tbody th{background:var(--panel);font-weight:600;white-space:nowrap;width:1%}
table.findings{table-layout:fixed}table.findings th:first-child{width:84px}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.badge{display:inline-block;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;padding:1px 7px;border-radius:10px;white-space:nowrap}
.green,.ok{color:var(--green);background:var(--green-bg)}
.amber,.warn{color:var(--amber);background:var(--amber-bg)}
.fail{color:var(--red);background:var(--red-bg)}
.info{color:var(--blue);background:var(--blue-bg)}
.grey,.note,.other{color:var(--grey);background:var(--grey-bg)}
.verdict .badge{margin-right:6px}
.mut{color:var(--mut)}
dl{margin:4px 0 0;display:grid;grid-template-columns:max-content 1fr;gap:2px 10px;font-size:13px}
dt{color:var(--mut)}dd{margin:0;overflow-wrap:anywhere}
.counts{margin:4px 0 8px}.counts .badge{margin-right:4px}
.good{color:var(--mut);margin:2px 0 6px;font-size:13px}
footer{margin-top:36px;padding-top:12px;border-top:1px solid var(--line);color:var(--mut);font-size:12.5px}
footer p{margin:4px 0}
@media print{body{padding:0}.wrap{overflow:visible}a{color:inherit}}
""".strip()

CSP = "default-src 'none'; style-src 'sha256-%s'; base-uri 'none'; form-action 'none'" % (
    base64.b64encode(hashlib.sha256(STYLE.encode("utf-8")).digest()).decode("ascii"))


def esc(s):
    """HTML-escaped text, quotes included."""
    return html.escape("" if s is None else str(s), quote=True)


def _plain(s):
    return CODE_RE.sub(r"<code>\1</code>", esc(s))


def text(s):
    """Escaped text; http(s) URLs become deep links, `x` becomes <code>x</code>."""
    s = "" if s is None else str(s)
    out, pos = [], 0
    for m in URL_RE.finditer(s):
        url = m.group(0).rstrip(".,;:)")
        out.append(_plain(s[pos:m.start()]))
        out.append('<a href="%s" rel="noreferrer">%s</a>' % (esc(url), esc(url)))
        pos = m.start() + len(url)
    out.append(_plain(s[pos:]))
    return "".join(out)


def badge(cls, label=None):
    return '<span class="badge %s">%s</span>' % (esc(cls), esc(label or cls))


def table(head, rows, numeric=(), cls=None):
    out = ['<div class="wrap"><table%s><thead><tr>' % (' class="%s"' % cls if cls else "")]
    out += ["<th>%s</th>" % esc(h) for h in head]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join('<td class="n">%s</td>' % c if i in numeric else "<td>%s</td>" % c
                                    for i, c in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def value(v):
    """Any JSON value as nested HTML, no interpretation."""
    if v is None:
        return '<span class="mut">null</span>'
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float, str)):
        return text(v)
    if isinstance(v, list):
        if not v:
            return '<span class="mut">none</span>'
        if all(not isinstance(x, (dict, list)) for x in v):
            return ", ".join("<code>%s</code>" % esc(x) for x in v)
        return "".join(value(x) for x in v)
    if not v:
        return '<span class="mut">none</span>'
    rows = "".join("<tr><th>%s</th><td>%s</td></tr>" % (esc(k), value(v[k])) for k in sorted(v))
    return '<div class="wrap"><table><tbody>%s</tbody></table></div>' % rows


def verdict(plan):
    cls = "green" if plan["decision"] == "green" else "unmeasured"
    label = "green cell" if plan["decision"] == "green" else "unmeasured"
    lines = "".join("<li>%s</li>" % text(s) for s in plan["summary"][1:])
    return '<div class="verdict %s">%s<strong>%s</strong><ul>%s</ul></div>' % (
        cls, badge("green" if cls == "green" else "amber", label), text(plan["summary"][0]), lines)


def targets(facts):
    rows = []
    for t in facts["targets"]:
        state = badge("ok", "usable") if t["usable"] else badge("fail", "not usable") + " " + text(t["reason"])
        rows.append(["<code>%s</code>" % esc(t["id"]), text(factsmod.describe(t)),
                     "<code>%s</code>" % esc(t["resource"]) if t["resource"] else '<span class="mut">none</span>',
                     state])
    return table(("target", "what it is", "resource", "state"), rows)


def considered(plan):
    if not plan["considered"]:
        return '<p class="mut">The matrix has no cells.</p>'
    rows = []
    for c in plan["considered"]:
        notes = [text(r) for r in c["reasons"]]
        if c["excluded"]:
            notes.append("excluded: " + text(c["excluded"]))
        rows.append(["<code>%s</code>" % esc(c["cell"]), badge(c["status"]),
                     "<code>%s</code>" % esc(c["target"]) if c["target"] else '<span class="mut">no target here</span>',
                     "yes" if c["eligible"] else "no",
                     "<br>".join(notes) or '<span class="mut">none</span>'])
    return table(("cell", "status", "target", "eligible", "reasons"), rows)


def findings(doc, title):
    counts = {}
    for f in doc["findings"]:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
    shown = " ".join(badge(k, "%d %s" % (counts[k], k)) for k in LEVELS if counts.get(k))
    out = ["<h2>%s</h2>" % esc(title),
           '<p class="counts">%s <span class="mut">exit %d</span></p>' % (shown or '<span class="mut">no findings</span>',
                                                                          doc["exit"])]
    # Sections in transcript order; findings printed before any section header
    # (stderr lines) come first under no heading.
    order = [None] + [s["name"] for s in doc["sections"]]
    info = {s["name"]: s for s in doc["sections"]}
    for name in order:
        group = [f for f in doc["findings"] if f["section"] == name]
        sec = info.get(name)
        if not group and not (sec and (sec["good"] or sec["src"])):
            continue
        if name is not None:
            out.append("<h3>%s</h3>" % text(name))
        if sec and sec["good"]:
            out.append('<p class="good">good: %s</p>' % text(sec["good"]))
        if sec and sec["src"]:
            out.append('<p class="good">sources: %s</p>' % text(sec["src"]))
        rows = []
        for f in group:
            cell = text(f["text"]) or '<span class="mut">(no text)</span>'
            details = [(k, f[k]) for k in DETAIL_KEYS if f[k]]
            if details:
                cell += "<dl>%s</dl>" % "".join("<dt>%s</dt><dd>%s</dd>" % (k, text(v)) for k, v in details)
            rows.append([badge(f["level"]), cell])
        if rows:
            out.append(table(("level", "finding"), rows, cls="findings"))
    return "".join(out)


def capacity(facts):
    rows = facts["capacity"]
    if not rows:
        return '<p class="mut">No capacity run recorded on this host; run <code>k3d-gpu prove</code>.</p>'
    body = []
    for r in rows:
        ci = "%s–%s" % tuple(r["ci95"]) if r["ci95"] else '<span class="mut">none</span>'
        body.append([esc(r["engine"]), esc(r["model"]), esc(r["precision"]), esc(r["agents_at_p95"]),
                     esc(r["slo_p95_s"]), esc(r["p95_s"]), ci, esc(r["reps"]), esc(r["measured_on"]),
                     "<code>%s</code>" % esc(r["report_id"])])
    return table(("engine", "model", "precision", "agents at p95", "SLO p95 s", "p95 s", "95% CI s", "reps",
                  "measured", "report"), body, numeric=(3, 4, 5, 6, 7))


def render(facts, plan):
    """report.html for one facts/v1 document and the plan/v1 made from it."""
    schema = factsmod.load_schema()
    schema_check.check(facts, schema)
    schema_check.check(plan, schema, "#/$defs/plan")
    if plan["fingerprint"] != facts["fingerprint"]:
        raise ValueError("plan is for %s, facts are for %s" % (plan["fingerprint"], facts["fingerprint"]))
    digest = hashlib.sha256(factsmod.dumps(facts)).hexdigest()
    host = facts["hostname"] or "(hostname not set)"
    cluster = facts["cluster"]
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta http-equiv="Content-Security-Policy" content="%s">' % html.escape(CSP, quote=False),
        '<meta name="referrer" content="no-referrer">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>k3d-gpu report: %s</title>" % esc(host),
        "<style>%s</style></head><body><main>" % STYLE,
        "<header><h1>k3d-gpu report: %s</h1>" % esc(host),
        '<p class="sub">facts/v1 <code>%s</code> · k3d-gpu %s · kernel %s</p></header>' % (
            esc(facts["fingerprint"]), esc(facts["release"]["k3d_gpu_version"]), esc(facts["host"]["kernel"])),
        "<h2>What runs on this box</h2>", verdict(plan),
        "<h2>Hardware targets</h2>", targets(facts),
        "<h2>Matrix cells considered</h2>", considered(plan),
        "<h2>Capacity</h2>", capacity(facts),
        findings(facts["doctor"], "doctor"),
        findings(facts["perf"], "doctor --perf"),
        "<h2>Cluster</h2>",
        "<p>%s</p>" % ("present, context <code>%s</code>" % esc(cluster["context"]) if cluster["present"]
                       else "not present: " + text(cluster["reason"])),
        value(cluster["nodes"]) if cluster["nodes"] else "",
        "<h2>Host inventory</h2>", value(facts["host"]),
        "<h2>Detected</h2>", value({d["vendor"]: d["resources"] for d in facts["detected"]}),
        "<h2>Node labels</h2>", value(facts["labels"]),
        "<h2>Release pins</h2>", value(facts["release"]["pins"]),
        "<footer>",
        "<p>Rendered from facts/v1, sha256 of its canonical bytes <code>%s</code>, and plan/v1.</p>" % digest,
        "<p>This page runs no script and loads nothing: its Content-Security-Policy is "
        "<code>default-src 'none'</code>. The links are references to follow, not resources it fetches.</p>",
        '<p><a href="%s" rel="noreferrer">%s</a></p>' % (DOCS, DOCS),
        "</footer></main></body></html>",
    ]
    return "\n".join(p for p in parts if p) + "\n"


def main(argv):
    ap = argparse.ArgumentParser(prog="report_html.py", description="report.html from facts/v1 on stdin")
    ap.add_argument("--matrix", default=os.path.join(HERE, "matrix.v1.json"))
    args = ap.parse_args(argv[1:])
    try:
        facts = json.load(sys.stdin)
        schema_check.check(facts, factsmod.load_schema())
        with open(args.matrix, encoding="utf-8") as f:
            matrix = json.load(f)
        schema_check.check(matrix, factsmod.load_schema(), "#/$defs/matrix")
        out = render(facts, planmod.plan(facts, matrix))
    except (ValueError, OSError) as e:
        sys.stderr.write("report: %s\n" % e)
        return 2
    sys.stdout.buffer.write(out.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

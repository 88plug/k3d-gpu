#!/usr/bin/env python3
"""Offline checks for report.html (docs/console-design.md, S4).

  offline_check.py static FILE...        the HTML can fetch nothing and holds no
                                         external URL except deep links
  offline_check.py netlog NETLOG.json    a browser that loaded the page made no
                                         request on the page's behalf

static: no <script>, no element that loads a resource (img, link, iframe, ...),
no src/srcset/poster/data/action/... attribute, no url(), @import or
@font-face in CSS, a Content-Security-Policy meta with default-src 'none', and
every http(s) URL in the file is an <a href> (a deep link) or the text of the
<a> that points at it.

netlog: Chromium's --log-net-log output. Requests the browser starts for
itself (component updates, sign-in probes) carry initiator "not an origin" and
a network isolation key of their own site; a request a file:// page starts
carries initiator "null" and the file:// key. Any of those is a failure.

Exit 0 when clean, 1 with one line per finding otherwise. Stdlib only.
"""

import html.parser
import json
import re
import sys

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
LOADING_TAGS = {"script", "link", "img", "iframe", "frame", "object", "embed", "video", "audio",
                "source", "track", "picture", "form", "base", "svg", "math", "applet", "portal", "input"}
LOADING_ATTRS = {"src", "srcset", "poster", "data", "action", "formaction", "background", "ping",
                 "manifest", "codebase", "longdesc", "xlink:href", "imagesrcset", "lowsrc", "dynsrc"}
CSS_FETCH_RE = re.compile(r"url\s*\(|@import|@font-face|image-set\s*\(", re.I)


def trim(url):
    return url.rstrip(".,;:)")


class Scan(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems = []
        self.anchor = []  # stack of hrefs of the open <a> elements
        self.csp = False
        self.in_style = False

    def bad(self, msg):
        line, _ = self.getpos()
        self.problems.append("line %d: %s" % (line, msg))

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in LOADING_TAGS:
            self.bad("<%s> element" % tag)
        if tag == "meta":
            if (a.get("http-equiv") or "").lower() == "refresh":
                self.bad("meta refresh")
            if (a.get("http-equiv") or "").lower() == "content-security-policy":
                directives = [d.strip() for d in (a.get("content") or "").split(";")]
                self.csp = "default-src 'none'" in directives
        if tag == "style":
            self.in_style = True
        for name, value in attrs:
            value = value or ""
            if name in LOADING_ATTRS:
                self.bad("%s attribute on <%s>" % (name, tag))
            elif name.startswith("on"):
                self.bad("event handler %s on <%s>" % (name, tag))
            elif name == "style" and CSS_FETCH_RE.search(value):
                self.bad("style attribute that fetches on <%s>" % tag)
            elif name == "href":
                if tag != "a":
                    self.bad("href on <%s>" % tag)
                elif not (value.startswith("#") or re.match(r"https?://[^\s]+$", value)):
                    self.bad("<a href=%r> is neither a fragment nor an http(s) deep link" % value)
            elif URL_RE.search(value):
                self.bad("URL in %s attribute of <%s>" % (name, tag))
        if tag == "a":
            self.anchor.append(a.get("href"))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag == "a":
            self.anchor.pop()

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor:
            self.anchor.pop()
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style and CSS_FETCH_RE.search(data):
            self.bad("CSS that fetches (url(), @import, @font-face or image-set)")
        for m in URL_RE.finditer(data):
            if not self.anchor or self.anchor[-1] != trim(m.group(0)):
                self.bad("URL outside its own deep link: %s" % m.group(0))

    def handle_pi(self, data):
        self.bad("processing instruction")


def static(text):
    s = Scan()
    s.feed(text)
    s.close()
    if not s.csp:
        s.problems.append("no Content-Security-Policy meta with default-src 'none'")
    return s.problems


def netlog(doc):
    types = {v: k for k, v in doc["constants"]["logEventTypes"].items()}
    problems = []
    for e in doc.get("events", []):
        if types.get(e.get("type")) != "URL_REQUEST_START_JOB":
            continue
        p = e.get("params", {})
        if "url" not in p:  # the job's end event repeats no request
            continue
        initiator, nik = p.get("initiator", ""), p.get("network_isolation_key", "")
        if initiator != "not an origin" or "file://" in nik:
            problems.append("page request: %s %s (initiator %s)" % (p.get("method"), p.get("url"), initiator))
    return problems


def main(argv):
    if len(argv) < 3 or argv[1] not in ("static", "netlog"):
        sys.stderr.write("usage: offline_check.py static FILE... | netlog NETLOG.json\n")
        return 2
    problems = []
    for path in argv[2:]:
        with open(path, encoding="utf-8") as f:
            found = static(f.read()) if argv[1] == "static" else netlog(json.load(f))
        problems += ["%s: %s" % (path, p) for p in found]
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

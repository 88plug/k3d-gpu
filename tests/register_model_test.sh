#!/usr/bin/env bash
# Runs examples/agents/litellm-gateway/register-model.sh against a stub gateway
# (python3 stdlib): a first registration POSTs model.json to /model/new, a model
# already listed by /models is skipped, and both hold whatever the JSON layout
# of model.json and of the /models response. No cluster, no container.

set -uo pipefail

SRC="$(cd "$(dirname "$0")/../examples/agents/litellm-gateway" && pwd)"
WORK="$(mktemp -d)"
trap 'kill "${STUB_PID:-}" 2>/dev/null; rm -rf "${WORK}"' EXIT

# Stub gateway: GET /models lists $WORK/listed (one id per line) as indented
# JSON; POST /model/new appends the body, one line each, to $WORK/posted. Needs the master key.
cat > "${WORK}/gateway.py" <<'EOF'
import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
WORK = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def _authed(self):
        if self.headers.get("Authorization") == "Bearer sk-test":
            return True
        self._send(401, {"error": "bad key"})
        return False
    def do_GET(self):
        if not self._authed(): return
        if self.path.split("?")[0] != "/models":
            return self._send(404, {})
        path = os.path.join(WORK, "listed")
        ids = open(path).read().split() if os.path.exists(path) else []
        self._send(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})
    def do_POST(self):
        if not self._authed(): return
        if self.path != "/model/new":
            return self._send(404, {})
        body = self.rfile.read(int(self.headers["Content-Length"]))
        with open(os.path.join(WORK, "posted"), "a") as f:
            f.write(json.dumps(json.loads(body)) + "\n")
        self._send(200, {"model_id": "x"})
httpd = HTTPServer(("127.0.0.1", 0), H)
print(httpd.server_address[1], flush=True)
httpd.serve_forever()
EOF
python3 "${WORK}/gateway.py" "${WORK}" > "${WORK}/port" &
STUB_PID=$!
for _ in $(seq 50); do [ -s "${WORK}/port" ] && break; sleep 0.1; done
GATEWAY="http://127.0.0.1:$(cat "${WORK}/port")"

PASS=0
FAIL=0
check() {  # check <description> <command...>
    local what="$1"; shift
    if "$@"; then echo "ok   ${what}"; PASS=$((PASS + 1)); else echo "FAIL ${what}"; FAIL=$((FAIL + 1)); fi
}

# run <layout> <listed ids...>: register-model.sh with model.json in that layout.
run() {
    local layout="$1"; shift
    rm -f "${WORK}/posted" "${WORK}/listed"
    for id in "$@"; do echo "${id}" >> "${WORK}/listed"; done
    mkdir -p "${WORK}/${layout}"
    cp "${SRC}/register-model.sh" "${WORK}/${layout}/"
    case "${layout}" in
        as-shipped) cp "${SRC}/model.json" "${WORK}/${layout}/model.json" ;;
        compact)    python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), separators=(",", ":")))' \
                        "${SRC}/model.json" > "${WORK}/${layout}/model.json" ;;
        indent4)    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d={"model_info": d.pop("model_info"), **d}; print(json.dumps(d, indent=4))' \
                        "${SRC}/model.json" > "${WORK}/${layout}/model.json" ;;
    esac
    OUT=$(GATEWAY="${GATEWAY}" LITELLM_MASTER_KEY=sk-test "${WORK}/${layout}/register-model.sh" 2>&1)
    RC=$?
}

posted_entry() {  # the POSTed body is model.json
    [ -f "${WORK}/posted" ] && python3 -c '
import json, sys
posted = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
sys.exit(0 if posted == [json.load(open(sys.argv[2]))] else 1)' "${WORK}/posted" "${SRC}/model.json"
}

NAME=Qwen3-4B-int4-ov
for layout in as-shipped compact indent4; do
    run "${layout}"
    check "${layout}: new model exits 0" [ "${RC}" -eq 0 ]
    check "${layout}: new model POSTs model.json to /model/new" posted_entry
    check "${layout}: reports ${NAME} registered" grep -q "^${NAME} registered" <<<"${OUT}"

    run "${layout}" "${NAME}"
    check "${layout}: listed model exits 0" [ "${RC}" -eq 0 ]
    check "${layout}: listed model is not POSTed" [ ! -e "${WORK}/posted" ]
    check "${layout}: reports ${NAME} already registered" grep -q "^${NAME} already registered" <<<"${OUT}"

    run "${layout}" "${NAME}-other" "other/${NAME}"
    check "${layout}: a different id containing the name still POSTs" posted_entry
done

rm -f "${WORK}/listed"
OUT=$(GATEWAY="${GATEWAY}" "${SRC}/register-model.sh" 2>&1)
check "missing LITELLM_MASTER_KEY fails" [ "$?" -ne 0 ]
check "missing LITELLM_MASTER_KEY is named" grep -q LITELLM_MASTER_KEY <<<"${OUT}"

echo "${PASS} passed, ${FAIL} failed"
[ "${FAIL}" -eq 0 ]

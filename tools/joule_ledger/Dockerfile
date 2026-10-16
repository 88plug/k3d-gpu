# Joule Ledger node daemon. Stdlib only, so nothing is installed at build or boot.
# Build from the repo root:
#   docker build -f tools/joule_ledger/Dockerfile -t "$IMAGE:$TAG" tools
FROM python:3.13.16-slim-trixie
COPY joule_ledger /opt/joule_ledger/joule_ledger
WORKDIR /opt/joule_ledger
ENTRYPOINT ["python3", "-m", "joule_ledger.daemon"]

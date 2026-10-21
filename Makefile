# Static validation for k3d-gpu. No cluster, no GPU, no running services.
#
#   make check            run every validator with the tools on PATH
#   make check-container  build .devcontainer/Dockerfile and run `make check` inside it
#                         (plain docker build: devcontainer.json features are not applied)
#
# The pinned toolchain lives in .devcontainer/Dockerfile.

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c

K8S_VERSION     ?= $(shell sed -nE 's/^ARG K3S_TAG="v([0-9.]+)-k3s.*/\1/p' Dockerfile)
DEV_IMAGE       ?= k3d-gpu-dev:local
MANIFEST_DIRS   := $(wildcard share examples)
# Helm values files (*.values.yaml) are not Kubernetes objects, so they are not validated here.
MANIFESTS       := $(shell find $(MANIFEST_DIRS) -type f \( -name '*.yml' -o -name '*.yaml' \) ! -name '*.values.yaml' 2>/dev/null | sort)
DOCKERFILES     := Dockerfile .devcontainer/Dockerfile


.PHONY: check test shellcheck hadolint kubeconform actionlint check-container dev-image

check: test shellcheck hadolint kubeconform actionlint
	@echo "make check: all validators passed"

test:
	tests/detect_test.sh
	tests/experimental_test.sh
	tests/phase_steer_test.sh
	tests/prefill_guard_test.sh
	tests/ci_wiring_test.sh
	tests/quickstart_test.sh
	tests/quickstart_adversarial_test.sh
	tests/fit_test.sh
	tests/llamacpp_test.sh
	python3 tests/fuzz/props.py

# Shell scripts: *.sh plus any file whose shebang runs sh or bash.
shellcheck:
	find . \( -name .git -o -name .claude \) -prune -o -type f -print | sort | while read -r f; do \
	  if [[ $$f == *.sh ]] || head -n1 "$$f" | grep -qE '^#!.*[/ ](ba)?sh( |$$)'; then echo "$$f"; fi; \
	done | xargs -r -t shellcheck --severity=warning

hadolint:
	hadolint --failure-threshold error $(DOCKERFILES)

# CRDs shipped in the manifests are converted to JSON schemas so their custom
# resources (e.g. BalloonsPolicy) validate strictly instead of being skipped.
# The CRD objects themselves are skipped: no upstream JSON schema exists for
# apiextensions.k8s.io/v1 CustomResourceDefinition in kubernetes-json-schema.
kubeconform:
	schemas=$$(mktemp -d); trap 'rm -rf "$$schemas"' EXIT; \
	(cd "$$schemas" && openapi2jsonschema $(abspath $(MANIFESTS)) >/dev/null); \
	kubeconform -strict -summary -kubernetes-version $(K8S_VERSION) \
	  -skip apiextensions.k8s.io/v1/CustomResourceDefinition,ray.io/v1/RayCluster \
	  -schema-location default \
	  -schema-location "$$schemas/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json" \
	  $(MANIFESTS)

# Workflow `run:` scripts get the same shellcheck severity (warning) as repo scripts.
actionlint:
	SHELLCHECK_OPTS=--severity=warning actionlint

dev-image:
	docker build -t $(DEV_IMAGE) .devcontainer

check-container: dev-image
	docker run --rm -v "$(CURDIR):/workspaces/k3d-gpu:ro" -w /workspaces/k3d-gpu $(DEV_IMAGE) make check

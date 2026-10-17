# Contributing to k3d-gpu

Thank you for your interest in contributing to k3d-gpu! We welcome contributions from the community.

## How to Contribute

### Reporting Bugs & Requests

This repository has the GitHub issue tracker and Discussions disabled. To report
a bug or request a feature, open a [Pull Request](https://github.com/88plug/k3d-gpu/pulls)
— either with a fix, or with a clear description and a minimal repro in the PR body.
Include relevant details:

- Your environment (OS, Docker version, k3d version, GPU model)
- Steps to reproduce (for bugs)
- Expected vs actual behavior
- Relevant logs or error messages

### Submitting Changes

1. **Fork the repository** and create a new branch from `main`
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**:
   - Keep changes focused and atomic
   - Follow existing code style and conventions
   - Update documentation if needed
   - Test your changes locally

3. **Test your changes**:

   Run the static checks first. They need no cluster or GPU: detection tests,
   shellcheck, hadolint, kubeconform over `share/` (and `examples/`), and
   actionlint. The pinned toolchain is in `.devcontainer/` (open the repo in a
   dev container or Codespace), or run everything in that container from any
   host with Docker:
   ```bash
   make check             # tools on PATH (inside the dev container)
   make check-container   # builds .devcontainer/Dockerfile (no features), runs make check
   ```

   Then test on real hardware:
   ```bash
   # Build the image
   ./build.sh
   
   # Test with k3d (--default-runtime=nvidia is required, or the cluster
   # advertises zero GPUs even though the node can see them — see README).
   # The device plugin is baked into the image, so no kubectl apply is needed.
   k3d cluster create test-gpu --image cryptoandcoffee/k3d-gpu --gpus all \
     --k3s-arg "--default-runtime=nvidia@server:*"

   # Verify GPUs are schedulable (must be > 0)
   kubectl get nodes -o jsonpath='{.items[*].status.allocatable.nvidia\.com/gpu}{"\n"}'
   
   # Cleanup
   k3d cluster delete test-gpu
   ```

4. **Commit your changes**:
   - Write clear, descriptive commit messages
   - Use conventional commit format when possible:
     - `feat:` for new features
     - `fix:` for bug fixes
     - `docs:` for documentation
     - `chore:` for maintenance tasks

5. **Push and create a Pull Request**:
   ```bash
   git push origin feature/your-feature-name
   ```
   - Provide a clear description of the changes
   - Reference any related issues
   - Ensure CI checks pass

## Development Guidelines

### Dockerfile Changes

- Keep the image size minimal
- Clean up package manager caches
- Use multi-stage builds when appropriate
- Pin versions for reproducibility
- Test on actual GPU hardware when possible

### Launcher Changes (`scripts/k3d-gpu`)

- `tests/detect_test.sh` holds focused cases built on small fake sysfs trees.
- `tests/sim_matrix_test.sh` runs `detect`, `doctor` and `up` (docker, k3d and
  kubectl stubbed) against every platform in `tests/fixtures/` — Gaudi 3, Panther
  Lake, Arrow Lake, Battlemage, Sapphire Rapids DSA/IAA/QAT/AMX, Raptor Lake +
  NVIDIA — and diffs the output, exit code, `k3d cluster create` arguments and
  kubectl calls against each platform's `*.golden` files. CI runs one job per platform.
- When a change is meant to alter that output, regenerate and review the goldens:
  `tests/sim_matrix_test.sh --update && git diff tests/fixtures`.
- Have hardware the matrix lacks? Capture it (reads ids, driver bindings, device
  node names, CPU flags, kernel release and firmware file names only):
  ```bash
  tests/fixtures/capture.sh tests/fixtures/<platform>/sysroot
  ```
  then add `SOURCES` (what the machine is), `allocatable` (`<resource> <count>`
  lines the device plugins report), an optional `env`, and run `--update`.

### Documentation

- Update README.md for user-facing changes
- Keep examples up to date
- Include clear explanations for complex configurations
- Back every performance claim in README.md or docs/ (tok/s, J/token, "Nx faster",
  "%", "first", "only", "fastest") on the same line with a primary-source URL,
  `[ledger:Hn]` (a SUPPORTED row of EXPERIMENTS.md) or `[provenance:Pn]` (a sourced
  row of docs/provenance.md). CI runs `tests/claims_check.sh` to enforce it
- The docs site renders README.md as its home page and this file as a page, so
  the quick start lives only in README.md. Longer pages go in `docs/*.md` and
  need a `nav` entry in `mkdocs.yml`; `mkdocs build --strict` fails on a page
  missing from `nav`. A `nav` entry whose page is not in `docs/` yet is skipped.
  Link repo files the GitHub way (`../Dockerfile`, `../scripts/k3d-gpu`); the
  site turns links that leave `docs/`, and links to directories, into GitHub links.
- Check the site with `tests/docs_test.sh` after
  `pip install --require-hashes -r docs-build/requirements.txt`

### Workflow Changes

- Test workflow changes in a fork first
- Use modern GitHub Actions syntax
- Add appropriate error handling
- Document any new secrets or environment variables needed

## Version Updates

The repository uses an automated workflow to update K3s and CUDA versions. Manual version changes should:

1. Update both `Dockerfile` ARG values
2. Update `README.md` environment variables table
3. Test the build with new versions
4. Update NVIDIA device plugin version if needed

## Code of Conduct

Please be respectful and constructive in all interactions. We aim to maintain a welcoming environment for all contributors.

## Questions?

Feel free to open an issue for questions or reach out to the maintainers.

## License

By contributing, you agree that your contributions will be licensed under the [FSL-1.1-ALv2](LICENSE.md) license.

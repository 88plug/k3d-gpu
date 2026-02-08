# Contributing to k3d-gpu

Thank you for your interest in contributing to k3d-gpu! We welcome contributions from the community.

## How to Contribute

### Reporting Issues

If you encounter a bug or have a feature request:

1. Check existing [Issues](https://github.com/88plug/k3d-gpu/issues) to avoid duplicates
2. Create a new issue with a clear title and description
3. Include relevant details:
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
   ```bash
   # Build the image
   ./build.sh
   
   # Test with k3d
   k3d cluster create test-gpu --image cryptoandcoffee/k3d-gpu --gpus all
   kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.18.2/nvidia-device-plugin.yml
   
   # Verify GPU access
   docker exec -it k3d-test-gpu-server-0 nvidia-smi
   
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

### Documentation

- Update README.md for user-facing changes
- Keep examples up to date
- Include clear explanations for complex configurations

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

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.

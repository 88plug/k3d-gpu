"""H43 live runner: SDK compatibility shim for the coding-agent-maf blueprint at 758d484.

The blueprint pins k8s-agent-sandbox 0.4.6 and calls
`SandboxClient.create_sandbox(template=..., namespace=..., [warmpool=...])`; 0.4.6 claims a
sandbox through the v1alpha1 SandboxClaim API, which agent-sandbox v1.0.7 (the version
`k3d-gpu sandbox up` installs) no longer serves. k8s-agent-sandbox 1.0.7 speaks v1beta1
but takes the warm pool as its first argument: `create_sandbox(warmpool, namespace=...)`.
This maps the old keywords onto the new call (warmpool, else template), so the agent code
runs unchanged. Loaded only by the H43 agent process (PYTHONPATH=bench/eatflow/coding).
"""
try:
    import k8s_agent_sandbox as _sdk
except ImportError:  # not the coding agent's interpreter
    _sdk = None


def _compat(orig):
    def create_sandbox(self, *args, template=None, warmpool=None, **kw):
        if args:
            return orig(self, *args, **kw)
        return orig(self, warmpool or template, **kw)
    create_sandbox.__wrapped__ = orig
    return create_sandbox


if _sdk is not None and not hasattr(_sdk.SandboxClient.create_sandbox, "__wrapped__"):
    _sdk.SandboxClient.create_sandbox = _compat(_sdk.SandboxClient.create_sandbox)

"""Span enrichment: the logic of the k3dgpu_energy collector processor.

A span is enriched only once every energy counter its pod touches has published
a reading after the span ended (plus latency); until then it stays pending.
Attributes sit under the vendor namespace k3dgpu.energy.*.

For MCP tool calls the span's parent context arrives in the request's
`_meta.traceparent` (MCP spec 2026-07-28, basic/index, "OpenTelemetry trace
context"), so a tool-server span joins the agent's trace with no extra wiring.
"""

METHOD = "fresh_edges_v1"
NS = "k3dgpu.energy"


def enrich_span(span, counters, pod_domains, attributions, cpu_split=None):
    """span: {"span_id", "start", "end", "attributes": {"k8s.pod.uid", ...}}.

    counters:     {domain: FreshCounter}, e.g. "nvidia.0", "rapl.core", "rapl.uncore"
    pod_domains:  {pod_uid: [domain, ...]} devices the pod is using
    attributions: {domain: {span_id: joules}} from ledger.attribute()
    cpu_split:    pe_split() result for the window, or None

    Returns the span with energy attributes added, or None while pending.
    """
    attrs = dict(span.get("attributes", {}))
    pod = attrs.get("k8s.pod.uid")
    domains = pod_domains.get(pod, [])
    out = {}
    for d in domains:
        b = counters[d].bounds(span["start"], span["end"])
        if b is None:
            return None
        lo, hi = b
        out[f"{NS}.{d}.joules"] = attributions.get(d, {}).get(span["span_id"], 0.0)
        out[f"{NS}.{d}.window_lo_joules"] = lo
        out[f"{NS}.{d}.window_hi_joules"] = hi
        out[f"{NS}.{d}.measured_fraction"] = (lo / hi) if hi > 0 else 1.0
    out[f"{NS}.method"] = METHOD
    if cpu_split and cpu_split.get("accepted"):
        out[f"{NS}.cpu.split"] = "pe_stable"
    else:
        # Shared VccIA rail with DVFS: P/E joules are not identifiable. Report
        # only the combined CPU energy and say why.
        out[f"{NS}.cpu.split"] = "unidentified"
        if cpu_split:
            out[f"{NS}.cpu.split_reason"] = cpu_split.get("reason", "")
    attrs.update(out)
    return {**span, "attributes": attrs}

---
name: network-diagnostics
description: Diagnose Linux network problems using net-diagnose, existing sos reports, or targeted sos collection. Use for NetworkManager, routing, DNS, Wi-Fi and VPN evidence; preserve the affected host and namespace context.
---

# Network diagnostics

Use `net-diagnose` from the selected nm-devtools checkout. Read
[diagnostic modes and evidence limits](../../docs/diagnostics.md) for the mode
being used. Sos modes need the checkout's `tools/net_diagnose_sos.py`; native
collection can use the standalone script.

If a sos report already exists, analyze its extracted directory first:

```sh
net-diagnose --from-sos /path/to/sosreport --summary
```

The directory must contain `sos_reports/manifest.json`. Import does not contact
the affected machine or execute archived commands. Do not start a VM or run
probes against captured addresses to fill import gaps. Preserve the original
report, source paths, capture times and unavailable evidence when explaining a
finding. Unknown source metadata must not be replaced with the analyst host's
details. A missing or unsupported snapshot is not evidence of healthy state.

Establish which machine and network namespace had the problem before collecting
fresh evidence. On that system, start with `net-diagnose --summary` in the
affected namespace. Native `--extended --logs` adds local evidence. An analyst's
workstation cannot fill gaps in another machine's report.
Use `--active` only for requested traffic; specify the application destination
with `--target`, `--dns-name` or `--url` so the default public probe is not used.
Keep ICMP, DNS, TCP and HTTP results scoped to the protocol and destination
actually measured.

For broader fresh support evidence, use
`net-diagnose --collector sos --sos-output NEWDIR --summary` on the affected
host. It requires installed sos 4.7.2 or newer, root and a verified match with PID 1's network
namespace. That match alone does not establish the physical host's namespace
inside a container. It captures system-wide evidence even with `--interface`;
that option only focuses analysis. Sos startup can also discover container
runtimes and enumerate devices inside named network namespaces.
Check the report's selected plugins and coverage notes: NetworkManager evidence
is unavailable when that sos installation does not list its plugin as enabled.
Do not install sos, elevate privileges or upload reports merely to analyze an
existing report. Follow the authorized task's scope for fresh collection.

Keep the private sos directory separate from the derived analysis. A failed or
interrupted capture may not have completed secret postprocessing. `--redact`
only pseudonymizes selected fields in the derived report; it does not clean the
source tree or establish that either artifact is safe to share.

Findings identify evidence and useful next checks. They do not establish a
reproducing regression, current connectivity or requirement conformance. For
VM captures, follow the [testvm lease and evidence workflow](../testvm/SKILL.md)
and retain evidence outside the guest before rollback, including the initial
rollback that `vm-test` normally performs.

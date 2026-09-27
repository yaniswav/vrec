# Security policy

## Supported versions

Only the latest released version of vrec is supported with security fixes. Please upgrade before
reporting an issue if you're not already on the latest release.

## Reporting a vulnerability

Please report security issues privately, using GitHub's "Report a vulnerability" button on the
[Security tab](../../security/advisories) of this repository (private security advisories). Do not
open a public issue for a security report.

## Scope

vrec runs entirely on your own machine and doesn't talk to any server of ours. In scope for a security
report is anything that could let another local process, a malicious page, or a network peer:

- read or exfiltrate your OBS WebSocket password or `data/` contents,
- take over or hijack your Chrome recording session or profile,
- escalate access beyond what vrec itself needs to do its job.

### Known design trade-off: the Chrome debugging port

vrec drives a dedicated Chrome profile over the DevTools remote-debugging protocol
(`[chrome] debug_port`, default `9222`). That port is bound to `localhost` only, but **any** local
process that can reach `localhost:9222` gets full control of that Chrome profile (navigation, script
execution, reading cookies/session data for whatever is logged into it). This is a known, accepted
trade-off of how Chrome's remote-debugging protocol works, not a vulnerability specific to vrec — it's
the same exposure any CDP-based tool has. Keep the recording profile dedicated to vrec (don't sign into
unrelated accounts in it), and don't expose that port beyond `localhost`.

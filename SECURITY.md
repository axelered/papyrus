# Security

Papyrus processes documents and web pages you don't control, so we take reports about it seriously.

## Reporting a vulnerability

Report vulnerabilities privately through
[GitHub security advisories](https://github.com/axelered/papyrus/security/advisories/new) or by email to
[oss@axelered.com](mailto:oss@axelered.com). Please don't open a public issue.

Include the Papyrus version (`GET /` returns it), your configuration without secrets, and a file or URL that
reproduces the problem if you have one. We confirm receipt within 3 working days and keep you updated until a fix is
released. We credit reporters in the release notes unless you ask us not to.

## Supported versions

Papyrus is before 1.0. We fix security issues in the latest minor release only, so upgrade to receive fixes.

## Threat model

Papyrus assumes that every document, URL and web page it processes may be hostile, and that the callers holding an
API key are trusted services. It has no users or per-key permissions.

What it does by default:

- Every converter runs in a separate worker process. A document that crashes or hangs a worker fails its own request,
  and the worker is replaced.
- URL sources, redirects and every resource a screenshot page loads are refused when they resolve to private,
  loopback, link-local or other internal addresses.
- Image files are checked against a pixel limit before decoding.
- LibreOffice runs no macros and loads no resources a document links to.
- Every request works in its own temporary folder, removed when the response is sent.
- Error responses never include stack traces, internal paths or OCR backend responses, and logs never include page
  content or keys.
- The container runs as a non-root user.

## Known limitations

These are open and tracked. Each has a mitigation in your deployment:

- Chromium runs without its own sandbox by default, because the default Docker seccomp profile blocks the user
  namespaces it needs. A Chromium exploit then runs as the Papyrus user, with access to its environment variables and
  to other requests' temporary files. Run the container with a seccomp profile that allows unprivileged user
  namespaces and set `browser.sandbox: true`.
- Plain URL downloads check the resolved address but don't pin it on connect, so DNS rebinding could reach an
  internal address. The browser does pin it. Set `http.allowed_hosts` for known sources, or block internal ranges with
  an egress policy.
- Worker processes have no memory limit, and the pixel limit doesn't cover images inside a PDF. Set a memory limit on
  the container.
- LibreOffice workers have network access outside the fetch policy. Deny egress to internal ranges when you process
  office files from untrusted sources.
- Multipart uploads are parsed before their size is checked. Enforce a body size limit at your reverse proxy.

Papyrus doesn't scan files for malware for now. If you need that, scan them before or after Papyrus.

The [security guide](https://docs.axelered.com/papyrus/security) has the full deployment checklist.

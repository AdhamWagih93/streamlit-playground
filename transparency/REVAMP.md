# Transparency Dashboard — revamped implementation

This directory now contains a working implementation of the system described in
[`ARCHITECTURE.md`](./ARCHITECTURE.md), [`PROJECT_SUMMARY.md`](./PROJECT_SUMMARY.md),
[`TECHNOLOGY.md`](./TECHNOLOGY.md) and [`TEST_CASES.md`](./TEST_CASES.md): a secure Spring Boot
proxy that serves one Power BI Report Server report, wrapped in a viewer shell.

Same job, same endpoints, same security posture. What changed is the quality of the viewer, the
strictness of the boundary, and the amount of the behaviour that is covered by tests.

## Running it

```bash
./samples/run-dev.sh --no-upstream    # quickest look, no report server needed
```

See [Connecting it to your report server](#connecting-it-to-your-report-server) for a real
deployment, and `--spring.profiles.active=local` for a developer machine with security off.

## Endpoints

Unchanged from the documented contract, plus one addition.

| Endpoint | Method | Purpose | Public |
|---|---|---|---|
| `/reports/powerbi/transparency` | GET | Viewer shell (HTML) | No |
| `/reports/powerbi/proxy/**` | GET, HEAD | Proxied report resources | No |
| `/reports/powerbi/status` | GET | **New** — upstream reachability for the status indicator | No |
| `/powerbi/**`, `/Reports/**`, `/api/**`, … | GET, HEAD | Viewer-bundle absolute paths, proxied in place | No |
| `/`, `/index.html` | GET | Redirect to the dashboard | Yes |
| `/favicon.ico`, `/.well-known/appspecific/com.chrome.devtools.json` | GET | 204 | Yes |
| `/actuator/health`, `/actuator/info`, `/actuator/prometheus` | GET | Health, build info, metrics | Yes |

## What the viewer looks like now

The documented behaviour was "HTML wrapper with iframe embedding" — a full-bleed frame and nothing
else. A frame on its own cannot say whether it is still loading, whether it failed, or whether the
figures on screen are current. So the shell has a job of its own now.

**Ink-dark chrome around a light report canvas.** The report is the content; the chrome is an
instrument frame that recedes. 56px app bar, 30px provenance strip, and the report fills everything
below.

**The provenance strip is the signature element.** A transparency dashboard that hides its own
chain of custody is missing its own point, so the chrome permanently states the report's path on the
source system, the time of the last successful fetch, and the fact that ownership metadata was
removed. Its `Details` panel adds the frame URL, the request id and the signed-in principal. This is
the same information the audit log records, shown to the person looking at the numbers.

**Honest states.** An iframe emits no progress and, once loaded, no failure. The shell watches three
signals instead — the frame's load event, a wall-clock deadline, and a cached upstream probe — and
resolves them into one state:

- *loading* — a ruling line is drawn across the provenance strip and the canvas explains what is happening
- *ready* — the status pill shows `Live` with the round-trip latency
- *unreachable* — the canvas explains what broke, offers `Try again`, and shows the request id to quote to support

If the source goes away while a report is on screen, the shell says so rather than letting a stale
canvas imply the figures are current.

**The rest of the quality floor.** Responsive to 390px (the chrome collapses, the title truncates,
touch-only controls drop away), light and dark themes with a `?theme=light|dark` pin for kiosk
displays, keyboard shortcuts (`R` reload, `F` full screen, `T` theme, `D` details, `Esc` close),
visible focus rings, `prefers-reduced-motion` honoured, and a print stylesheet that drops the chrome.

Error responses follow the caller rather than the exception: a browser gets a designed page in the
same visual language, an XHR or API client gets RFC 7807 `application/problem+json`. Both carry the
correlation id.

## Connecting it to your report server

Two ways in; pick one.

**Container / env vars** — `samples/powerbi.env.sample`

```bash
cp samples/powerbi.env.sample samples/powerbi.env   # fill in host, account, password
./samples/run-dev.sh                                # build + start, prints the URL
```

`samples/powerbi.env` is git-ignored: it holds a real password. In production these come from the
platform's secret store, not a file.

**File config for `java -jar`** — `samples/application-onprem.yml.sample`

```bash
cp samples/application-onprem.yml.sample config/application.yml   # edit
java -jar target/efinance-powerbi-0.0.1-SNAPSHOT.jar
```

Spring Boot loads `./config/application.yml` automatically and it overrides the packaged defaults,
so a setting change needs a restart, not a rebuild.

The four values that actually matter:

| Setting | What to put in it |
|---|---|
| `base-url` / `POWERBI_BASE_URL` | Scheme, host and port of the report server. No path, no trailing slash. |
| `allowed-host` / `POWERBI_ALLOWED_HOST` | The same host again. This is the allow-list — a request that resolves anywhere else is refused before a socket opens. |
| `transparency-report-path` / `POWERBI_REPORT_PATH` | The report's path in the portal, e.g. `/Reports/powerbi/Transparency`. Use `report-url` instead if the report needs query parameters such as `rs:Embed=true`. |
| `auth-type` + credentials | `NTLM` for a Windows domain account (the usual on-prem case), `BASIC` for a service account, `NONE` for local testing. The account can be written `DOMAIN\user` or as a bare username with `domain` set separately. |

Both samples also carry a stop-gap `spring.security.user` sign-in so the dashboard is reachable
before an identity provider is wired up. Replace it with LDAP or OIDC before real users arrive —
a single shared account defeats the audit trail, which logs who opened the report.

### Spinning up a dev container

```bash
./samples/run-dev.sh                 # against the report server in samples/powerbi.env
./samples/run-dev.sh --no-upstream   # UI only — no report server, no credentials needed
PORT=9090 ./samples/run-dev.sh       # somewhere other than 8080
```

Or with compose:

```bash
docker compose up --build
```

Either way the jar is built inside Maven, so no JDK is needed on the host; the first build downloads
dependencies and takes a few minutes, later ones reuse the cache.

`--no-upstream` is the one to use while working on the shell: it starts with no report server at all,
which puts the status pill in `Unreachable` and the canvas in its recovery state — the states hardest
to reach on purpose.

If the report server resolves only from your workstation (a VPN route, an internal DNS suffix), the
container needs `--network host`, `--add-host`, or a `dns:` entry — see the commented block in
`compose.yaml`.

## What changed under the hood

| Area | Before (as documented) | Now |
|---|---|---|
| CSP | One policy with `unsafe-inline` and `unsafe-eval` for the whole app | Split: shell pages get a strict per-request nonce policy, proxy paths keep the loose policy the viewer bundle needs |
| Error responses | `ProblemDetail` JSON for everything | Content-negotiated: designed HTML page or `problem+json` |
| Path policy | Block-list of known-bad paths | Deny by default — allowed prefixes first, then administrative collections, unkeyed enumerations, portal browsing and OData metadata |
| Traversal defence | Repeated decoding | Repeated decoding **and** `..`, backslash, control-character and embedded-credential rejection |
| Host allow-list | Configured | Enforced on the resolved URI before a socket opens |
| Upstream cookies | `PBIRS_` cookies forwarded | Forwarded *and* hardened: `Domain` stripped, `HttpOnly`, `SameSite=Lax`, `Secure` on TLS |
| Proxied HTML | Served as received | Absolute upstream URLs rewritten to this origin, `<base>` removed, so no asset escapes the proxy |
| Response size | Unbounded | Capped (`max-response-size`, default 32MB) — a runaway response fails the request instead of the JVM |
| Rate limiting | Listed as a future enhancement | Per-client token bucket in front of the proxy, so one runaway viewer cannot drain the connection pool |
| Correlation | None | `X-Request-Id` on every response, in the MDC, in the audit line, and on every error surface |
| Metrics | None | `powerbi.proxy.request` timer tagged by outcome and status, via `/actuator/prometheus` |
| Actuator exposure | `health`, `info`, `env`, `beans` | `health`, `info`, `prometheus` — `env` and `beans` disclose configuration and topology |
| Sanitised fields | 8 ownership fields | 12, matched case-insensitively, depth-bounded, byte-identical passthrough when nothing matched |
| Tests | 2 test classes | 6 classes, 85 tests, including a full-context integration test of the shell and its headers |

Two documented behaviours were kept deliberately even though they look like defects: `/favicon.ico`
still answers `204` (the shell links an SVG icon instead), and the proxy still accepts only `GET`
and `HEAD` — widening it to `POST` would put the report server's write surface behind a CSRF
exemption.

## Tests

```
mvn test
```

85 tests, mapped to the IDs in `TEST_CASES.md`:

| Class | Covers |
|---|---|
| `ProxyPathSecurityPolicyTest` | SC-001…SC-006, RG-002 |
| `SensitiveJsonSanitizerTest` | SC-004, CC-001, RG-003 |
| `HeaderPolicyTest` | RG-001, SS-004, IT-004 |
| `UpstreamLinkRewriterTest` | SS-001, SS-003 |
| `PowerBiReportPropertiesTest` | CFG-001…CFG-004 |
| `TransparencyShellIntegrationTest` | FC-001, FC-004, FC-005, FC-006, SEC-002, SEC-003, EH-001, IT-002 |

One regression test is worth naming: blocking `/home` as a substring also blocked
`/Reports/assets/js/home.js`, which is a real asset name. Portal paths are matched as prefixes now,
and the test pins that.

## Known limits

- **NTLM.** Apache HttpClient 5 has deprecated NTLM; `NTCredentials` still works on the pinned
  5.3.x, but if the report server can be fronted by Kerberos or a Windows-auth gateway, prefer that.
- **URLs built in JavaScript.** HTML rewriting cannot reach URLs the viewer bundle assembles at
  runtime, which is why the bundle's absolute paths (`/api/**`, `/Reports/**`, …) are proxied in
  place rather than redirected.
- **Rate-limit state is per instance.** Behind more than one replica the effective limit multiplies;
  move the bucket to a shared store if that matters.

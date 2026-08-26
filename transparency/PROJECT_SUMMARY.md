# Project Summary

## Overview

**Project Name:** efinance-powerbi  
**Artifact ID:** efinance-powerbi  
**Version:** 0.0.1-SNAPSHOT  
**Group ID:** eg.com.efinance

## Goals

1. **Secure Proxy for Power BI Report Server**: Provide a secure proxy layer for embedding Power BI Report Server reports in web applications.

2. **Transparency Dashboard**: Enable access to Power BI transparency reports through a dedicated endpoint.

3. **Authentication Support**: Support multiple authentication types (BASIC, NTLM, NONE) for connecting to Power BI Report Server.

4. **Security Hardening**: Implement comprehensive security measures including:
   - Spring Security integration
   - CSRF protection with selective exemptions
   - Content Security Policy (CSP) headers
   - X-Frame-Options for clickjacking prevention
   - Optional application-level authentication

5. **Production-Ready Deployment**: Provide Docker support for containerized deployment.

## Scope

### In Scope
- Proxying Power BI Report Server requests
- Serving transparency reports
- Handling authentication (BASIC/NTLM)
- Centralized exception handling
- Health check endpoints
- Docker containerization

### Out of Scope
- Power BI Report Server administration
- Custom report development
- Power BI Premium features
- Real-time analytics processing

## Key Features

### 1. Proxy Functionality
- Transparent proxying of Power BI Report Server resources
- Header filtering to prevent information leakage
- Cookie handling for session persistence
- Support for various Power BI URL patterns

### 2. Transparency Reports
- Dedicated endpoint for transparency reports
- HTML wrapper with iframe embedding
- Audit logging for access tracking

### 3. Security
- Optional authentication (disabled by default for local testing)
- CSRF protection with Power BI path exemptions
- Content Security Policy enforcement
- HTTP Strict Transport Security (HSTS)
- Frame options protection

### 4. Configuration
- Externalized configuration via properties
- Support for multiple authentication types
- Configurable timeouts and connection settings
- Allowed resource path prefixes

### 5. Observability
- Actuator endpoints for health checks
- Startup configuration logging
- Structured exception handling
- Audit logging for transparency report access

## Technical Stack

| Technology | Version | Purpose |
|----------|---------|---------|
| Java | 17 | Runtime |
| Spring Boot | 3.3.5 | Application framework |
| Spring Security | 3.3.5 | Security |
| Apache HttpClient5 | Latest | HTTP client |
| Jsoup | 1.18.1 | HTML parsing |
| Maven | Latest | Build tool |

## Project Status

**Current Status:** Active Development

**Last Updated:** 2024

## Dependencies

### Build Dependencies
- Spring Boot Starter Parent (3.3.5)
- Spring Boot Starter Web
- Spring Boot Starter Security
- Spring Boot Starter Validation
- Spring Boot Starter Actuator
- Spring Boot Configuration Processor

### Runtime Dependencies
- Apache HttpClient5
- Jsoup

### Test Dependencies
- Spring Boot Starter Test
- Spring Security Test

## Deployment Options

1. **Docker Container**: Use provided Dockerfile for containerized deployment
2. **Maven Jar**: Build executable JAR with Spring Boot Maven plugin
3. **Direct Deployment**: Deploy JAR to application server

## Future Enhancements

- Enhanced audit logging
- Rate limiting
- Request/response caching
- Custom authentication providers
- Metrics and monitoring enhancements
- Health check customizations

## Contact

For questions or issues, refer to the project repository or documentation.


## Detailed Features

### 1. Proxy Functionality - Deep Dive

#### Header Management
- **Hop-by-Hop Headers:** Automatically stripped from requests to prevent information leakage
  - connection, keep-alive, proxy-authenticate, proxy-authorization
  - te, trailer, transfer-encoding, upgrade, host
  - authorization, cookie, origin, referer
  - accept-encoding, sec-fetch-* headers
  - cache-control, pragma, priority, content-length

- **Response Headers Filtered:** Prevents upstream server information leakage
  - Same as hop-by-hop plus:
  - x-frame-options, content-security-policy
  - x-aspnet-version, x-reportserver-version
  - x-sql-reporting-services-version

#### Cookie Handling
- PBIRS_ prefixed cookies are preserved for session persistence
- Cookies are forwarded to maintain user sessions with Power BI Report Server

#### Path Support
The proxy supports multiple URL patterns:
- `/powerbi/**` - Power BI viewer paths
- `/PowerBI/**` - Case-insensitive Power BI paths
- `/ReportServer/**` - Report Server paths
- `/Reserved.ReportViewerWebControl.axd` - Reserved viewer control
- `/Reports/**` - Report paths
- `/api/**` - API endpoints
- `/explore/**` - Explore paths
- `/modelsAndExploration/**` - Model paths
- `/querydata/**` - Query data paths
- `/metadata/**` - Metadata paths
- `/resources/**` - Resource paths
- `/public/**` - Public paths
- `/13.0.*/**` - Version-specific paths

### 2. Transparency Reports - Deep Dive

#### Report Wrapper
The transparency report endpoint returns an HTML page with:
- Responsive iframe embedding
- Full-screen display
- No caching (Cache-Control: no-store)
- XSS protection via HTML entity encoding

#### Report URL Construction
- Uses configured `reportUrl` from properties
- Falls back to `baseUrl + transparencyReportPath`
- Validates against allowed hosts

### 3. Security - Deep Dive

#### Authentication Modes

**BASIC Authentication:**
- Uses UsernamePasswordCredentials
- Simple username/password pair
- Suitable for most deployments

**NTLM Authentication:**
- Uses NTCredentials with domain/workstation
- Multi-step handshake requires persistent connections
- Connection pool configured for NTLM (max 20 total, 10 per route)
- Connection eviction after 5 minutes idle

**NONE (No Authentication):**
- Used for local testing
- All requests permitted
- Not recommended for production

#### Security Configuration Options

| Property | Default | Description |
|----------|---------|-------------|
| app.security.enabled | true | Enable application-level auth |
| powerbi.report-server.authType | BASIC | Auth type: BASIC, NTLM, NONE |
| powerbi.report-server.connectTimeout | 5s | Connection timeout |
| powerbi.report-server.responseTimeout | 60s | Response timeout |

#### CSRF Exemptions
CSRF protection is disabled for:
- All Power BI proxy paths
- Favicon endpoint
- Chrome DevTools metadata endpoint
- Actuator health endpoints
- Root path and index.html

### 4. Configuration - Deep Dive

#### PowerBiReportProperties

| Property | Type | Required | Description |
|----------|------|----------|-------------|
| baseUrl | String | Yes | Power BI Report Server URL |
| username | String | Yes | Authentication username |
| password | String | Yes | Authentication password |
| domain | String | No | NTLM domain |
| workstation | String | No | NTLM workstation |
| authType | AuthType | Yes | BASIC, NTLM, or NONE |
| allowedHost | String | Yes | Allowed host for proxying |
| reportUrl | String | No | Report URL |
| transparencyReportPath | String | Yes | Transparency report path |
| allowedResourcePathPrefixes | List | Yes | Allowed paths |
| connectTimeout | Duration | Yes | Connection timeout |
| responseTimeout | Duration | Yes | Response timeout |

#### Username Parsing
The configuration handles NTLM usernames in domain\username format:
- Extracts domain from username if present
- Normalizes username for logging
- Handles backslash separators

### 5. Observability - Deep Dive

#### Startup Logging
On application startup, the following information is logged:
- Base URL of Power BI Report Server
- Allowed host
- Report URL (if configured)
- Transparency report path
- Authentication type
- Whether username is configured
- Whether domain is configured
- Whether workstation is configured

For NTLM authentication, additional logging includes:
- Normalized domain
- Normalized username
- Workstation (if configured)

#### Exception Handling
All exceptions are converted to ProblemDetail responses:
- ForbiddenProxyRequestException → 403
- AccessDeniedException → 403
- ReportServerUnavailableException → 502
- NoResourceFoundException → 404
- Any other Exception → 500

## API Endpoints Summary

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---------------|
| /reports/powerbi/transparency | GET | Open transparency report | Yes (if enabled) |
| /reports/powerbi/proxy/** | GET | Proxy report resource | Yes (if enabled) |
| /powerbi/** | GET | Proxy viewer resource | Yes (if enabled) |
| /favicon.ico | GET | Return no content | No |
| /.well-known/appspecific/com.chrome.devtools.json | GET | Chrome DevTools metadata | No |
| /actuator/health/** | GET | Health check | No |
| / | GET | Root path | No |
| /index.html | GET | Index page | No |

## Error Codes

| Code | Status | Description |
|------|--------|-------------|
| 403 | FORBIDDEN | Access denied or forbidden path |
| 404 | NOT_FOUND | Resource not found |
| 500 | INTERNAL_SERVER_ERROR | Unexpected server error |
| 502 | BAD_GATEWAY | Upstream server unavailable |

## Compliance Considerations

- **Data Minimization:** Sensitive fields removed from JSON responses
- **Clickjacking Prevention:** X-Frame-Options: SAMEORIGIN
- **XSS Prevention:** Content Security Policy enforced
- **Information Leakage Prevention:** Response headers filtered
- **Audit Trail:** Transparency report access logged

## Future Enhancements

- [ ] Add rate limiting
- [ ] Implement request signing
- [ ] Add request/response tracing
- [ ] Support for OAuth2 authentication
- [ ] Add metrics collection
- [ ] Implement circuit breaker pattern
- [ ] Add request caching
- [ ] Support for multiple upstream servers

## Related Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) - System design and data flow
- [TECHNOLOGY.md](./TECHNOLOGY.md) - Technology stack
- [TEST_CASES.md](./TEST_CASES.md) - Test cases
- [README.md](./README.md) - Project overview



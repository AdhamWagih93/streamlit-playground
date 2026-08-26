# Test Cases

## Overview

This document contains comprehensive test cases for the efinance-powerbi proxy application. Test cases cover main functions, edge cases, error handling, and security scenarios.

## Test Case Format

Each test case includes:
- **ID:** Unique identifier
- **Title:** Brief description
- **Category:** Functional/Security/Performance/Error Handling
- **Priority:** P0 (Critical), P1 (High), P2 (Medium), P3 (Low)
- **Preconditions:** Required setup
- **Steps:** Test execution steps
- **Expected Result:** Expected outcome
- **Actual Result:** (To be filled during testing)
- **Status:** Pass/Fail/Blocked

---

## Functional Test Cases

### FC-001: Transparency Report Access

| Field | Value |
|-------|---------|
| **ID** | FC-001 |
| **Title** | Access transparency report endpoint |
| **Category** | Functional |
| **Priority** | P0 |
| **Preconditions** | Application running, Power BI configured |
| **Steps** | 1. Send GET request to `/reports/powerbi/transparency`<br>2. Verify authentication (if enabled)<br>3. Check response HTML structure |
| **Expected Result** | Returns HTML with iframe pointing to transparency report URL |
| **Actual Result** | |
| **Status** | |

### FC-002: Proxy Report Resource

| Field | Value |
|-------|---------|
| **ID** | FC-002 |
| **Title** | Proxy Power BI report resource |
| **Category** | Functional |
| **Priority** | P0 |
| **Preconditions** | Application running, upstream server accessible |
| **Steps** | 1. Send GET request to `/reports/powerbi/proxy/{path}`<br>2. Include valid authentication headers<br>3. Verify response content |
| **Expected Result** | Returns proxied content from Power BI Report Server |
| **Actual Result** | |
| **Status** | |

### FC-003: Redirect Power BI Viewer Paths

| Field | Value |
|-------|---------|
| **ID** | FC-003 |
| **Title** | Redirect various Power BI viewer paths |
| **Category** | Functional |
| **Priority** | P0 |
| **Preconditions** | Application running |
| **Steps** | 1. Send GET request to `/powerbi/{path}`<br>2. Send GET request to `/PowerBI/{path}`<br>3. Send GET request to `/ReportServer/{path}`<br>4. Verify redirect/proxy behavior |
| **Expected Result** | All paths proxied to upstream server |
| **Actual Result** | |
| **Status** | |

### FC-004: Favicon Handling

| Field | Value |
|-------|---------|
| **ID** | FC-004 |
| **Title** | Handle favicon.ico request |
| **Category** | Functional |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send GET request to `/favicon.ico`<br>2. Verify response status |
| **Expected Result** | Returns 204 No Content |
| **Actual Result** | |
| **Status** | |

### FC-005: Chrome DevTools Metadata

| Field | Value |
|-------|---------|
| **ID** | FC-005 |
| **Title** | Handle Chrome DevTools metadata request |
| **Category** | Functional |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send GET request to `/.well-known/appspecific/com.chrome.devtools.json`<br>2. Verify response status |
| **Expected Result** | Returns 204 No Content |
| **Actual Result** | |
| **Status** | |

### FC-006: Health Check Endpoint

| Field | Value |
|-------|---------|
| **ID** | FC-006 |
| **Title** | Access health check endpoint |
| **Category** | Functional |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send GET request to `/actuator/health`<br>2. Verify response structure |
| **Expected Result** | Returns health status JSON |
| **Actual Result** | |
| **Status** | |

---

## Authentication Test Cases

### AUTH-001: BASIC Authentication

| Field | Value |
|-------|---------|
| **ID** | AUTH-001 |
| **Title** | BASIC authentication to Power BI server |
| **Category** | Authentication |
| **Priority** | P0 |
| **Preconditions** | Power BI configured with BASIC auth |
| **Steps** | 1. Configure `authType=BASIC`<br>2. Set username and password<br>3. Send authenticated request<br>4. Verify successful proxy |
| **Expected Result** | Request authenticated and proxied successfully |
| **Actual Result** | |
| **Status** | |

### AUTH-002: NTLM Authentication

| Field | Value |
|-------|---------|
| **ID** | AUTH-002 |
| **Title** | NTLM authentication to Power BI server |
| **Category** | Authentication |
| **Priority** | P0 |
| **Preconditions** | Power BI configured with NTLM auth |
| **Steps** | 1. Configure `authType=NTLM`<br>2. Set domain, username, workstation<br>3. Send authenticated request<br>4. Verify successful proxy |
| **Expected Result** | NTLM handshake completed, request proxied |
| **Actual Result** | |
| **Status** | |

### AUTH-003: No Authentication

| Field | Value |
|-------|---------|
| **ID** | AUTH-003 |
| **Title** | Proxy without authentication |
| **Category** | Authentication |
| **Priority** | P1 |
| **Preconditions** | Power BI configured with no auth |
| **Steps** | 1. Configure `authType=NONE`<br>2. Send unauthenticated request<br>3. Verify successful proxy |
| **Expected Result** | Request proxied without credentials |
| **Actual Result** | |
| **Status** | |

### AUTH-004: Application Security Disabled

| Field | Value |
|-------|---------|
| **ID** | AUTH-004 |
| **Title** | Access without authentication when security disabled |
| **Category** | Authentication |
| **Priority** | P1 |
| **Preconditions** | `app.security.enabled=false` |
| **Steps** | 1. Set `app.security.enabled=false`<br>2. Send unauthenticated request<br>3. Verify access granted |
| **Expected Result** | Request allowed without authentication |
| **Actual Result** | |
| **Status** | |

### AUTH-005: Application Security Enabled

| Field | Value |
|-------|---------|
| **ID** | AUTH-005 |
| **Title** | Deny access when security enabled without auth |
| **Category** | Authentication |
| **Priority** | P0 |
| **Preconditions** | `app.security.enabled=true` |
| **Steps** | 1. Set `app.security.enabled=true`<br>2. Send unauthenticated request<br>3. Verify access denied |
| **Expected Result** | Request denied with 401/403 |
| **Actual Result** | |
| **Status** | |

---

## Error Handling Test Cases

### EH-001: Forbidden Proxy Request

| Field | Value |
|-------|---------|
| **ID** | EH-001 |
| **Title** | Handle forbidden proxy request |
| **Category** | Error Handling |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to disallowed path<br>2. Verify exception thrown<br>3. Check response status |
| **Expected Result** | Returns 403 FORBIDDEN with error message |
| **Actual Result** | |
| **Status** | |

### EH-002: Report Server Unavailable

| Field | Value |
|-------|---------|
| **ID** | EH-002 |
| **Title** | Handle upstream server unavailable |
| **Category** | Error Handling |
| **Priority** | P0 |
| **Preconditions** | Upstream server unreachable |
| **Steps** | 1. Simulate upstream server failure<br>2. Send request to proxy<br>3. Verify exception handling |
| **Expected Result** | Returns 502 BAD_GATEWAY with error message |
| **Actual Result** | |
| **Status** | |

### EH-003: Access Denied

| Field | Value |
|-------|---------|
| **ID** | EH-003 |
| **Title** | Handle access denied exception |
| **Category** | Error Handling |
| **Priority** | P1 |
| **Preconditions** | Application security enabled |
| **Steps** | 1. Send unauthorized request<br>2. Verify AccessDeniedException<br>3. Check response status |
| **Expected Result** | Returns 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

### EH-004: Resource Not Found

| Field | Value |
|-------|---------|
| **ID** | EH-004 |
| **Title** | Handle resource not found |
| **Category** | Error Handling |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to non-existent resource<br>2. Verify NoResourceFoundException<br>3. Check response status |
| **Expected Result** | Returns 404 NOT_FOUND |
| **Actual Result** | |
| **Status** | |

### EH-005: Unexpected Server Error

| Field | Value |
|-------|---------|
| **ID** | EH-005 |
| **Title** | Handle unexpected application error |
| **Category** | Error Handling |
| **Priority** | P0 |
| **Preconditions** | Application running |
| **Steps** | 1. Trigger unhandled exception<br>2. Verify exception caught<br>3. Check response status |
| **Expected Result** | Returns 500 INTERNAL_SERVER_ERROR |
| **Actual Result** | |
| **Status** | |

---

## Security Test Cases

### SEC-001: CSRF Protection

| Field | Value |
|-------|---------|
| **ID** | SEC-001 |
| **Title** | CSRF token validation |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | CSRF enabled |
| **Steps** | 1. Send POST request without CSRF token<br>2. Verify request rejected<br>3. Send request with valid token<br>4. Verify request accepted |
| **Expected Result** | CSRF token validated, invalid requests rejected |
| **Actual Result** | |
| **Status** | |

### SEC-002: Content Security Policy

| Field | Value |
|-------|---------|
| **ID** | SEC-002 |
| **Title** | CSP header enforcement |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to any endpoint<br>2. Verify CSP header present<br>3. Verify header matches expected policy |
| **Expected Result** | CSP header with restricted sources |
| **Actual Result** | |
| **Status** | |

### SEC-003: X-Frame-Options

| Field | Value |
|-------|---------|
| **ID** | SEC-003 |
| **Title** | X-Frame-Options header |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to any endpoint<br>2. Verify X-Frame-Options header<br>3. Verify value is SAMEORIGIN |
| **Expected Result** | X-Frame-Options: SAMEORIGIN |
| **Actual Result** | |
| **Status** | |

### SEC-004: HSTS Header

| Field | Value |
|-------|---------|
| **ID** | SEC-004 |
| **Title** | HTTP Strict Transport Security |
| **Category** | Security |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to any endpoint<br>2. Verify Strict-Transport-Security header<br>3. Verify max-age value |
| **Expected Result** | HSTS header with appropriate max-age |
| **Actual Result** | |
| **Status** | |

### SEC-005: Cookie Security

| Field | Value |
|-------|---------|
| **ID** | SEC-005 |
| **Title** | Cookie HttpOnly flag |
| **Category** | Security |
| **Priority** | P2 |
| **Preconditions** | CSRF enabled |
| **Steps** | 1. Check CSRF cookie attributes<br>2. Verify HttpOnly flag |
| **Expected Result** | HttpOnly=false (required for JS access) |
| **Actual Result** | |
| **Status** | |

---

## Performance Test Cases

### PER-001: Connection Pool Utilization

| Field | Value |
|-------|---------|
| **ID** | PER-001 |
| **Title** | Connection pool under load |
| **Category** | Performance |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send concurrent requests<br>2. Monitor connection pool metrics<br>3. Verify no connection exhaustion |
| **Expected Result** | Pool handles concurrent requests efficiently |
| **Actual Result** | |
| **Status** | |

### PER-002: Timeout Handling

| Field | Value |
|-------|---------|
| **ID** | PER-002 |
| **Title** | Timeout behavior |
| **Category** | Performance |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Configure short timeouts<br>2. Send request to slow upstream<br>3. Verify timeout behavior |
| **Expected Result** | Request times out gracefully |
| **Actual Result** | |
| **Status** | |

---

## Edge Case Test Cases

### EC-001: Empty Request Body

| Field | Value |
|-------|---------|
| **ID** | EC-001 |
| **Title** | Handle empty request body |
| **Category** | Edge Case |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send GET request with empty body<br>2. Verify request processed |
| **Expected Result** | Request processed normally |
| **Actual Result** | |
| **Status** | |

### EC-002: Very Long URL

| Field | Value |
|-------|---------|
| **ID** | EC-002 |
| **Title** | Handle very long URL path |
| **Category** | Edge Case |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request with long path<br>2. Verify request processed |
| **Expected Result** | Request processed or appropriate error |
| **Actual Result** | |
| **Status** | |

### EC-003: Special Characters in URL

| Field | Value |
|-------|---------|
| **ID** | EC-003 |
| **Title** | Handle special characters in URL |
| **Category** | Edge Case |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request with special chars<br>2. Verify URL encoding handled |
| **Expected Result** | Special characters properly encoded |
| **Actual Result** | |
| **Status** | |

### EC-004: Multiple Cookies

| Field | Value |
|-------|---------|
| **ID** | EC-004 |
| **Title** | Handle multiple PBIRS_ cookies |
| **Category** | Edge Case |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request with multiple cookies<br>2. Verify cookies forwarded |
| **Expected Result** | All valid cookies forwarded |
| **Actual Result** | |
| **Status** | |

### EC-005: Large Response

| Field | Value |
|-------|---------|
| **ID** | EC-005 |
| **Title** | Handle large response from upstream |
| **Category** | Edge Case |
| **Priority** | P2 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to large report<br>2. Verify response streamed |
| **Expected Result** | Large response handled without memory issues |
| **Actual Result** | |
| **Status** | |

---

## Integration Test Cases

### INT-001: Full Proxy Flow

| Field | Value |
|-------|---------|
| **ID** | INT-001 |
| **Title** | Complete proxy request flow |
| **Category** | Integration |
| **Priority** | P0 |
| **Preconditions** | Application running, upstream configured |
| **Steps** | 1. Send authenticated request<br>2. Verify upstream call<br>3. Verify response returned |
| **Expected Result** | Complete flow works end-to-end |
| **Actual Result** | |
| **Status** | |

### INT-002: Transparency Report Flow

| Field | Value |
|-------|---------|
| **ID** | INT-002 |
| **Title** | Complete transparency report flow |
| **Category** | Integration |
| **Priority** | P0 |
| **Preconditions** | Application running, transparency configured |
| **Steps** | 1. Access transparency endpoint<br>2. Verify HTML generated<br>3. Verify iframe src correct |
| **Expected Result** | Transparency report served correctly |
| **Actual Result** | |
| **Status** | |

### INT-003: Startup Configuration

| Field | Value |
|-------|---------|
| **ID** | INT-003 |
| **Title** | Application startup logging |
| **Category** | Integration |
| **Priority** | P2 |
| **Preconditions** | Application configured |
| **Steps** | 1. Start application<br>2. Verify startup logs<br>3. Verify configuration logged |
| **Expected Result** | Configuration logged on startup |
| **Actual Result** | |
| **Status** | |

---

## Test Execution Notes

### Environment Setup

1. **Build Project:**
   ```bash
   cd /Transparency_Dashboard
   mvn clean package
   ```

2. **Configure Application:**
   - Set `application.properties` with Power BI credentials
   - Configure `app.security.enabled` as needed

3. **Run Tests:**
   ```bash
   mvn test
   ```

### Test Data

- **Test User:** `testuser`
- **Test Password:** `testpassword`
- **Test Domain:** `TESTDOMAIN`
- **Test Workstation:** `TESTWS`

### Tools

- **HTTP Client:** curl, Postman, or browser
- **Load Testing:** JMeter or k6
- **Security Scanning:** OWASP ZAP

### Reporting

- Update `Actual Result` and `Status` columns after each test
- Document any bugs or issues found
- Track test coverage percentage

---

## Appendix: Test Case Templates

### New Functional Test Case Template

```
### FC-XXX: [Title]

| Field | Value |
|-------|---------|
| **ID** | FC-XXX |
| **Title** | [Brief description] |
| **Category** | Functional |
| **Priority** | P0/P1/P2/P3 |
| **Preconditions** | [Required setup] |
| **Steps** | [Numbered steps] |
| **Expected Result** | [Expected outcome] |
| **Actual Result** | |
| **Status** | |
```

### New Error Handling Test Case Template

```
### EH-XXX: [Title]

| Field | Value |
|-------|---------|
| **ID** | EH-XXX |
| **Title** | [Error scenario] |
| **Category** | Error Handling |
| **Priority** | P0/P1/P2 |
| **Preconditions** | [Setup] |
| **Steps** | [Steps] |
| **Expected Result** | [Expected outcome] |
| **Actual Result** | |
| **Status** | |
```


## Security Test Cases

### SC-001: Blocked OData Metadata Endpoints

| Field | Value |
|-------|------|
| **ID** | SC-001 |
| **Title** | Block OData metadata endpoints |
| **Category** | Security |
| **Priority** | P0 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send GET to `/api/v1.0`<br>2. Send GET to `/api/v2.0/`<br>3. Send GET to `/api/v2.0/%24metadata`<br>4. Send GET to `/api/v2.0/%2524metadata` |
| **Expected Result** | All requests return 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

### SC-002: Blocked Administrative Resources

| Field | Value |
|-------|------|
| **ID** | SC-002 |
| **Title** | Block administrative API resources |
| **Category** | Security |
| **Priority** | P0 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send GET to `/api/v2.0/DataSources`<br>2. Send GET to `/api/v2.0/Telemetry`<br>3. Send GET to `/api/v2.0/System/ReportServerRelativeUrl`<br>4. Send GET to `/api/v2.0/CatalogItems`<br>5. Send GET to `/api/v2.0/PowerBIReports` |
| **Expected Result** | All requests return 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

### SC-003: Blocked Browse and Home Paths

| Field | Value |
|-------|------|
| **ID** | SC-003 |
| **Title** | Block folder browsing paths |
| **Category** | Security |
| **Priority** | P0 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send GET to `/reports/browse`<br>2. Send GET to `/reports/home`<br>3. Send GET to `/reports/home/folder` |
| **Expected Result** | All requests return 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

### SC-004: Allowed Keyed Report Resources

| Field | Value |
|-------|------|
| **ID** | SC-004 |
| **Title** | Allow keyed report resources |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send GET to `/api/v2.0/PowerBIReports(123)`<br>2. Send GET to `/Reports/powerbi/public/report`<br>3. Send GET to `/Reports/assets/js/runtime.js` |
| **Expected Result** | All requests succeed (200 OK) |
| **Actual Result** | |
| **Status** | |

### SC-005: URL Decoding Attack Prevention

| Field | Value |
|-------|------|
| **ID** | SC-005 |
| **Title** | Prevent URL decoding attacks |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send GET to `/api/v2.0/%ZZ`<br>2. Send GET to `/api/v2.0/%2524metadata`<br>3. Verify repeated decoding limit (3 iterations) |
| **Expected Result** | Requests with invalid encoding return 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

### SC-006: Null and Blank Path Handling

| Field | Value |
|-------|------|
| **ID** | SC-006 |
| **Title** | Handle null and blank paths |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send GET to `/api/v2.0/` (empty path)<br>2. Send GET to `/api/v2.0/ ` (space) |
| **Expected Result** | Requests with null/blank paths return 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

## Error Handling Test Cases

### EH-001: Upstream Server Unavailable

| Field | Value |
|-------|------|
| **ID** | EH-001 |
| **Title** | Handle upstream server unavailable |
| **Category** | Error Handling |
| **Priority** | P0 |
| **Preconditions** | Application running, upstream server down |
| **Steps** | 1. Stop Power BI Report Server<br>2. Send GET to `/reports/powerbi/proxy/{path}`<br>3. Verify response |
| **Expected Result** | Returns 502 BAD_GATEWAY with error message |
| **Actual Result** | |
| **Status** | |

### EH-002: Unexpected Server Error

| Field | Value |
|-------|------|
| **ID** | EH-002 |
| **Title** | Handle unexpected server errors |
| **Category** | Error Handling |
| **Priority** | P0 |
| **Preconditions** | Application running, error condition triggered |
| **Steps** | 1. Trigger unhandled exception<br>2. Send request to affected endpoint<br>3. Verify response |
| **Expected Result** | Returns 500 INTERNAL_SERVER_ERROR with generic message |
| **Actual Result** | |
| **Status** | |

### EH-003: Resource Not Found

| Field | Value |
|-------|------|
| **ID** | EH-003 |
| **Title** | Handle resource not found |
| **Category** | Error Handling |
| **Priority** | P1 |
| **Preconditions** | Application running, resource doesn't exist |
| **Steps** | 1. Send GET to `/reports/powerbi/proxy/nonexistent`<br>2. Verify response |
| **Expected Result** | Returns 404 NOT_FOUND |
| **Actual Result** | |
| **Status** | |

### EH-004: Access Denied

| Field | Value |
|-------|------|
| **ID** | EH-004 |
| **Title** | Handle access denied |
| **Category** | Error Handling |
| **Priority** | P0 |
| **Preconditions** | Application running, security enabled, user not authenticated |
| **Steps** | 1. Send GET to `/reports/powerbi/transparency` without auth<br>2. Verify response |
| **Expected Result** | Returns 403 FORBIDDEN |
| **Actual Result** | |
| **Status** | |

## Performance Test Cases

### PF-001: Connection Pool Reuse

| Field | Value |
|-------|------|
| **ID** | PF-001 |
| **Title** | Verify connection pool reuse |
| **Category** | Performance |
| **Priority** | P2 |
| **Preconditions** | Application running, multiple concurrent requests |
| **Steps** | 1. Send 100 concurrent requests<br>2. Monitor connection pool usage<br>3. Verify connection reuse |
| **Expected Result** | Connections reused, no new connections created for each request |
| **Actual Result** | |
| **Status** | |

### PF-002: Timeout Handling

| Field | Value |
|-------|------|
| **ID** | PF-002 |
| **Title** | Verify timeout handling |
| **Category** | Performance |
| **Priority** | P1 |
| **Preconditions** | Application running, upstream server slow |
| **Steps** | 1. Configure short timeout<br>2. Send request to slow upstream<br>3. Verify timeout behavior |
| **Expected Result** | Request times out after configured duration |
| **Actual Result** | |
| **Status** | |

### PF-003: Response Size Limits

| Field | Value |
|-------|------|
| **ID** | PF-003 |
| **Title** | Handle large responses |
| **Category** | Performance |
| **Priority** | P2 |
| **Preconditions** | Application running, large report available |
| **Steps** | 1. Send request for large report<br>2. Monitor memory usage<br>3. Verify response delivery |
| **Expected Result** | Large responses handled without OOM |
| **Actual Result** | |
| **Status** | |

## Integration Test Cases

### IT-001: Full Proxy Flow

| Field | Value |
|-------|------|
| **ID** | IT-001 |
| **Title** | Complete proxy request flow |
| **Category** | Integration |
| **Priority** | P0 |
| **Preconditions** | Application running, upstream server accessible |
| **Steps** | 1. Send request to `/powerbi/{path}`<br>2. Verify request forwarded to upstream<br>3. Verify response headers filtered<br>4. Verify response content correct |
| **Expected Result** | Request proxied, headers filtered, content returned |
| **Actual Result** | |
| **Status** | |

### IT-002: Transparency Report Flow

| Field | Value |
|-------|------|
| **ID** | IT-002 |
| **Title** | Complete transparency report flow |
| **Category** | Integration |
| **Priority** | P0 |
| **Preconditions** | Application running, transparency report available |
| **Steps** | 1. Send GET to `/reports/powerbi/transparency`<br>2. Verify HTML response<br>3. Verify iframe src correct<br>4. Verify no caching headers |
| **Expected Result** | HTML with iframe, correct src, no-cache headers |
| **Actual Result** | |
| **Status** | |

### IT-003: Authentication Flow

| Field | Value |
|-------|------|
| **ID** | IT-003 |
| **Title** | Complete authentication flow |
| **Category** | Integration |
| **Priority** | P0 |
| **Preconditions** | Application running, security enabled, credentials configured |
| **Steps** | 1. Configure BASIC or NTLM auth<br>2. Send authenticated request<br>3. Verify upstream authentication<br>4. Verify response received |
| **Expected Result** | Request authenticated, upstream accepts, response returned |
| **Actual Result** | |
| **Status** | |

### IT-004: Cookie Persistence

| Field | Value |
|-------|------|
| **ID** | IT-004 |
| **Title** | Cookie persistence across requests |
| **Category** | Integration |
| **Priority** | P1 |
| **Preconditions** | Application running, session cookies set |
| **Steps** | 1. Send request to set session cookie<br>2. Send subsequent request<br>3. Verify cookie forwarded<br>4. Verify session maintained |
| **Expected Result** | Cookie forwarded, session maintained |
| **Actual Result** | |
| **Status** | |

## Regression Test Cases

### RG-001: Header Filtering Regression

| Field | Value |
|-------|------|
| **ID** | RG-001 |
| **Title** | Verify header filtering regression |
| **Category** | Regression |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request to proxy endpoint<br>2. Inspect response headers<br>3. Verify hop-by-hop headers removed<br>4. Verify sensitive headers removed |
| **Expected Result** | All hop-by-hop and sensitive headers removed |
| **Actual Result** | |
| **Status** | |

### RG-002: Path Validation Regression

| Field | Value |
|-------|------|
| **ID** | RG-002 |
| **Title** | Verify path validation regression |
| **Category** | Regression |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send requests to various paths<br>2. Verify allowed paths succeed<br>3. Verify blocked paths fail<br>4. Verify URL decoding handled correctly |
| **Expected Result** | Allowed paths succeed, blocked paths fail |
| **Actual Result** | |
| **Status** | |

### RG-003: JSON Sanitization Regression

| Field | Value |
|-------|------|
| **ID** | RG-003 |
| **Title** | Verify JSON sanitization regression |
| **Category** | Regression |
| **Priority** | P1 |
| **Preconditions** | Application running, JSON response available |
| **Steps** | 1. Send request returning JSON<br>2. Inspect response JSON<br>3. Verify sensitive fields removed<br>4. Verify required fields preserved |
| **Expected Result** | Sensitive fields removed, required fields preserved |
| **Actual Result** | |
| **Status** | |

## Security Scan Test Cases

### SS-001: XSS Prevention

| Field | Value |
|-------|------|
| **ID** | SS-001 |
| **Title** | Verify XSS prevention |
| **Category** | Security Scan |
| **Priority** | P0 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request with XSS payload in path<br>2. Verify payload encoded in response<br>3. Verify no script execution |
| **Expected Result** | Payload encoded, no script execution |
| **Actual Result** | |
| **Status** | |

### SS-002: Clickjacking Prevention

| Field | Value |
|-------|------|
| **ID** | SS-002 |
| **Title** | Verify clickjacking prevention |
| **Category** | Security Scan |
| **Priority** | P0 |
| **Preconditions** | Application running |
| **Steps** | 1. Attempt to frame page from different origin<br>2. Verify X-Frame-Options header<br>3. Verify page cannot be framed |
| **Expected Result** | X-Frame-Options: SAMEORIGIN, page cannot be framed |
| **Actual Result** | |
| **Status** | |

### SS-003: CSP Enforcement

| Field | Value |
|-------|------|
| **ID** | SS-003 |
| **Title** | Verify CSP enforcement |
| **Category** | Security Scan |
| **Priority** | P0 |
| **Preconditions** | Application running |
| **Steps** | 1. Inspect response headers<br>2. Verify Content-Security-Policy present<br>3. Verify policy directives correct<br>4. Attempt to load external resources |
| **Expected Result** | CSP header present, external resources blocked |
| **Actual Result** | |
| **Status** | |

### SS-004: Information Leakage Prevention

| Field | Value |
|-------|------|
| **ID** | SS-004 |
| **Title** | Verify information leakage prevention |
| **Category** | Security Scan |
| **Priority** | P0 |
| **Preconditions** | Application running |
| **Steps** | 1. Inspect response headers<br>2. Verify server header removed<br>3. Verify framework headers removed<br>4. Verify version information removed |
| **Expected Result** | Server, framework, and version headers removed |
| **Actual Result** | |
| **Status** | |

## Compliance Test Cases

### CC-001: Data Minimization

| Field | Value |
|-------|------|
| **ID** | CC-001 |
| **Title** | Verify data minimization |
| **Category** | Compliance |
| **Priority** | P1 |
| **Preconditions** | Application running |
| **Steps** | 1. Send request returning user data<br>2. Inspect response<br>3. Verify sensitive fields removed<br>4. Verify only required data returned |
| **Expected Result** | Sensitive fields removed, only required data returned |
| **Actual Result** | |
| **Status** | |

### CC-002: Audit Logging

| Field | Value |
|-------|------|
| **ID** | CC-002 |
| **Title** | Verify audit logging |
| **Category** | Compliance |
| **Priority** | P1 |
| **Preconditions** | Application running, logging configured |
| **Steps** | 1. Access transparency report<br>2. Check audit logs<br>3. Verify access logged<br>4. Verify user and timestamp recorded |
| **Expected Result** | Access logged with user and timestamp |
| **Actual Result** | |
| **Status** | |

### CC-003: Access Control

| Field | Value |
|-------|------|
| **ID** | CC-003 |
| **Title** | Verify access control |
| **Category** | Compliance |
| **Priority** | P0 |
| **Preconditions** | Application running, security enabled |
| **Steps** | 1. Send request without authentication<br>2. Verify access denied<br>3. Send request with authentication<br>4. Verify access granted |
| **Expected Result** | Unauthenticated requests denied, authenticated requests granted |
| **Actual Result** | |
| **Status** | |

## Test Execution Instructions

### Prerequisites

1. Application deployed and running
2. Power BI Report Server accessible
3. Configuration properties set
4. Test database or mock server configured

### Execution Steps

1. **Setup:**
   - Start application
   - Configure test environment
   - Verify upstream server accessible

2. **Functional Tests:**
   - Execute FC-001 through FC-006
   - Verify all pass
   - Document any failures

3. **Security Tests:**
   - Execute SC-001 through SC-006
   - Verify all pass
   - Document any failures

4. **Error Handling Tests:**
   - Execute EH-001 through EH-004
   - Verify all pass
   - Document any failures

5. **Performance Tests:**
   - Execute PF-001 through PF-003
   - Verify all pass
   - Document any failures

6. **Integration Tests:**
   - Execute IT-001 through IT-004
   - Verify all pass
   - Document any failures

7. **Regression Tests:**
   - Execute RG-001 through RG-003
   - Verify all pass
   - Document any failures

8. **Security Scan Tests:**
   - Execute SS-001 through SS-004
   - Verify all pass
   - Document any failures

9. **Compliance Tests:**
   - Execute CC-001 through CC-003
   - Verify all pass
   - Document any failures

### Reporting

After execution, update the TEST_CASES.md file with:
- Actual results for each test case
- Status (Pass/Fail/Blocked)
- Notes on any issues encountered
- Recommendations for improvements

## Test Automation

### JUnit Test Structure

```java
package eg.com.efinance.powerbi.service;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ProxyPathSecurityPolicyTest {
    @Test
    void testBlockedPath() {
        assertThrows(ForbiddenProxyRequestException.class, 
            () -> policy.validate("/api/v1.0"));
    }
}
```

### Test Coverage Goals

- **Functional Tests:** 100% of controller endpoints
- **Security Tests:** 100% of security policies
- **Error Handling:** All exception types covered
- **Edge Cases:** Null, empty, invalid inputs

### Continuous Integration

- Tests run on every commit
- Failing tests block deployment
- Coverage reports generated
- Performance benchmarks tracked


---

## Security Test Cases

### SC-001: Metadata Endpoint Blocking

| Field | Value |
|-------|-------|
| **ID** | SC-001 |
| **Title** | Block $metadata endpoint access |
| **Category** | Security |
| **Priority** | P0 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send GET request to `/api/v1.0/$metadata`<br>2. Verify response status<br>3. Check error message content |
| **Expected Result** | Returns 403 FORBIDDEN with "not publicly available" message |
| **Actual Result** | |
| **Status** | |

### SC-002: Admin Resource Blocking

| Field | Value |
|-------|-------|
| **ID** | SC-002 |
| **Title** | Block admin resource enumeration |
| **Category** | Security |
| **Priority** | P0 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send GET request to `/api/v1.0/alertsubscriptions`<br>2. Verify response status<br>3. Check error message content |
| **Expected Result** | Returns 403 FORBIDDEN for admin resources (alertsubscriptions, schedules, subscriptions) |
| **Actual Result** | |
| **Status** | |

### SC-003: Browse Reports Blocking

| Field | Value |
|-------|-------|
| **ID** | SC-003 |
| **Title** | Block folder browsing access |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send GET request to `/reports/browse`<br>2. Verify response status<br>3. Check error message content |
| **Expected Result** | Returns 403 FORBIDDEN for browse/home paths |
| **Actual Result** | |
| **Status** | |

### SC-004: Ownership Information Sanitization

| Field | Value |
|-------|-------|
| **ID** | SC-004 |
| **Title** | Remove ownership fields from JON responses |
| **Category** | Security/Privacy |
| **Priority** | P1 |
| **Preconditions** | Application running, catalog endpoint accessible |
| **Steps** | 1. Access `/api/v1.0/catalogitems`<br>2. Inspect response body<br>3. Verify sensitive fields removed |
| **Expected Result** | CreatedBy/ModifiedBy/CreatedDate/MODIFIEDDATE and nested username fields are stripped from JSON |
| **Actual Result** | |
| **Status** | |

### SC-005: URL Decoding Attack Prevention

| Field | Value |
|-------|-------|
| **I** | SC-005 |
| **Title** | Prevent double-decode attacks |
| **Category** | Security |
| **Priority** | P1 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send request with encoded path containing `+` characters<br>2. Verify proper handling of form-encoded spaces<br>3. Check for decode loop prevention |
| **Expected Result** | Limits decoding to maximum 3 iterations; preserves `+` as literal character in paths |
| **Actual Result** | |
| **Status** | |

---

## Error Handliest Cases

### EH-001: Upstream Server Unavailable

| Field | Value |
|-------|-------|
| **ID** | EH-001 |
| **Title** | Handle upstream server timeout/unreachable |
| **Category** | Error Handling |
| **Priority** | P1 |
| **Preconditions** | Application running, upstream configured but unreachable |
| **Steps** | 1. Stop or block Power BI Report Server<br>2. Send request to proxy endpoint<br>3. Verify error response format and status code |
| **Expected Result** | Returns 502 BAD_GATEWAY with descriptive message via ProblemDetail |
| **Actual Result** | |
| **Status** | |

### EH-002: Authentication Failure Handling

| Field | Value |
|-------|-------|
| **ID** | EH-002 |
| **Title** | Handle authentication challenge responses |
| **Category** | Error Handling |
| **Priority** | P1 |
| **Preconditions** | Application running, invalid credentials configured |
| **Steps** | 1. Send request with incorrect username/password<br>2. Verify response status code<br>3. Check error message content |
| **Expected Resu| Returns appropriate HTTP 401/403 based on auth type; no stack trace leakage |
| **Actual Result** | |
| **Status** | |

### EH-003: Connection Pool Exhaustion

| Field | Value |
|-------|-------|
| **ID** | EH-003 |
| **Title** | Handle connection pool exhaustion gracefully |
| **Category** | Error Handling/Performance |
| **Priority** | P2 |
| **Preconditions** | Application running, high concurrent load simulated |
| **Steps** | 1. Generate many simultaneous requests<br>2. Monitor connection manager stavia logging<br>3. Verify graceful degradation behavior |
| **Expected Result** | Connection pool evicts idle/expired connections; maintains reasonable throughput under stress |
| **Actual Result** | |
| **Status** | |

### EH-004: Invalid Path Handling

| Field | Value |
|-------|-------|
| **ID** | EH-004 |
| **Title** | Handle null/blank path requests |
| **Category** | Error Handling |
| **Priority** | P2 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send request with empty/null path parameter<br>2. Verify response status code<br>3. Check error message content |
| **Expected Result** | Returns 403 FORBIDDEN for null/blank paths; throws ForbiddenProxyRequestException |
| **Actual Result** | |
| **Status** | |

### EH-005: Unexpected Exception Handling

| Field | Value |
|-------|-------|
| **ID** | EH-005 |
| **Title** | Handle unexpected application errors gracefully |
| **Category** | Error Handling/Logging |
| **Priority** | P1 |
| **Preconditions** | Application running, error condition triggered programmatically |
| **Steps** | 1. Trigger unhandled exception in service layer<br>2. Verify response status code<br>3. Check that stack trace is not exposed to client |
| **Expected Result** | Returns 500 INTERNAL_SERVER_ERROR with generic message; logs full error details server-side |
| **Actual Result** | |
| **Status** | |

---

## Performance Test Cases

### PF-001: Connection Reuse Verification

| Field | Value |
|-------|-------|
| **ID** | PF-001 |
| **Title** | Verify ion pooling effectiveness |
| **Category** | Performance |
| **Priority** | P2 |
| **Preconditions** | Application running, proxy configured with pool settings |
| **Steps** | 1. Send multiple sequential requests<br>2. Monitor PoolStats via logging or metrics endpoint<br>3. Verify connection reuse rate exceeds threshold |
| **Expected Result** | Connection manager reuses connections; evicts idle after 5 minutes as configured |
| **Actual Result** | |
| **Status** | |

### PF-002: Timeout Configuration Valid| Field | Value |
|-------|-------|
| **ID** | PF-002 |
| **Title** | Verify timeout settings are applied correctly |
| **Category** | Performance/Configuration |
| **Priority** | P2 |
| **Preconditions** | Application running with custom timeouts configured |
| **Steps** | 1. Configure connectTimeout=5s, responseTimeout=60s<br>2. Send request that takes >5s to establish connection<br>3. Verify timeout behavior matches configuration |
| **Expected Result** | Connection attempts fail after 5 seconds; responsuncated at 60 seconds |
| **Actual Result** | |
| **Status** | |

---

## Integration Test Cases

### IT-001: Full Proxy Flow with Authentication

| Field | Value |
|-------|-------|
| **ID** | IT-001 |
| **Title** | End-to-end proxy request flow |
| **Category** | Integration |
| **Priority** | P0 |
| **Preconditions** | Application running, upstream server accessible with valid credentials |
| **Steps** | 1. Configure Power BI Report Server URL and auth<br>2. Send authenticated GET to `/reports/powerbi/pry/{path}`<br>3. Verify response content matches upstream (minus filtered headers) |
| **Expected Result** | Successful proxy; response body identical except for hop-by-hop header removal |
| **Actual Result** | |
| **Status** | |

### IT-002: Transparency Report Generation Flow

| Field | Value |
|-------|-------|
| **ID** | IT-002 |
| **Title** | End-to-end transparency report flow |
| **Category** | Integration |
| **Priority** | P1 |
| **Preconditions** | Application running, upstream server accessible walid credentials |
| **Steps** | 1. Send GET to `/reports/powerbi/transparency`<br>2. Verify HTML response contains iframe<br>3. Validate iframe src points to configured transparency report path |
| **Expected Result** | Returns well-formed HTML with properly encoded iframe source URL |
| **Actual Result** | |
| **Status** | |

### IT-003: Multiple Path Pattern Matching

| Field | Value |
|-------|-------|
| **ID** | IT-003 |
| **Title** | Verify all redirect patterns work correctly |
| **Category** | Integation/Functional |
| **Priority** | P1 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Test each pattern: `/powerbi/**`, `/PowerBI/**`, `/ReportServer/**`<br>2. Verify all redirect to upstream correctly<br>3. Check that non-matching paths return appropriate errors |
| **Expected Result** | All listed patterns proxy successfully; unknown paths handled by exception handlers |
| **Actual Result** | |
| **Status** | |

---

## Configuration Test Cases

### CFG-001: AuthType BASIalidation

| Field | Value |
|-------|-------|
| **ID** | CFG-001 |
| **Title** | Verify BASIC authentication configuration works |
| **Category** | Configuration/Security |
| **Priority** | P2 |
| **Preconditions** | Application configured with authType=BASIC, valid credentials |
| **Steps** | 1. Set powerbi.report-server.authType=BASIC<br>2. Configure username/password<br>3. Start application and verify startup logs show BASIC config |
| **Expected Result** | Application starts successfully; HttpClient ussicCredentialsProvider |
| **Actual Result** | |
| **Status** | |

### CFG-002: AuthType NTLM Validation

| Field | Value |
|-------|-------|
| **ID** | CFG-002 |
| **Title** | Verify NTLM authentication configuration works |
| **Category** | Configuration/Security |
| **Priority** | P2 |
| **Preconditions** | Application configured with authType=NTLM, valid credentials including domain/workstation |
| **Steps** | 1. Set powerbi.report-server.authType=NTLM<br>2. Configure username/password/domain/workstatior>3. Verify startup logs show NTLM identity shape |
| **Expected Result** | Application starts successfully; HttpClient registers NTLMSchemeFactory and uses NTLM auth schemes |
| **Actual Result** | |
| **Status** | |

### CFG-003: AuthType NONE Validation (Local Testing)

| Field | Value |
|-------|-------|
| **ID** | CFG-003 |
| **Title** | Verify no-auth configuration for local testing |
| **Category** | Configuration/Security |
| **Priority** | P2 |
| **Preconditions** | Application configured with authType=NONE, app.security.enabled=false |
| **Steps** | 1. Set powerbi.report-server.authType=NONE<br>2. Disable application security via property<br>3. Start application and verify no UserDetailsService bean created |
| **Expected Result** | Application starts successfully; permits all requests for local proxy testing |
| **Actual Result** | |
| **Status** | |

### CFG-004: Allowed Resource Path Prefixes Validation

| Field | Value |
|-------|-------|
| **ID** | CFG-004 |
| **Title** | Verify allowed paths are proxied correctly |
| **Category** | Configuration/Functional |
| **Priority** | P2 |
| **Preconditions** | Application running with custom allowedResourcePathPrefixes configured |
| **Steps** | 1. Configure powerbi.report-server.allowedResourcePathPrefixes<br>2. Send requests to paths in and outside the list<br>3. Verify proxy behavior matches configuration |
| **Expected Result** | Only specified prefixes are proxied; others handled by exception handlers or redirect controller |
| **Actual Result** | | **Status** | |

---

## Edge Case Test Cases

### EC-001: Path with Special Characters

| Field | Value |
|-------|-------|
| **ID** | EC-001 |
| **Title** | Handle paths containing special characters safely |
| **Category** | Edge Case/Security |
| **Priority** | P2 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send request with path containing `&`, `"`, `<`, `>`<br>2. Verify proper HTML entity encoding in transparency report<br>3. Check that special characters don't breparsing |
| **Expected Result** | Special characters properly escaped; no XSS vulnerabilities introduced |
| **Actual Result** | |
| **Status** | |

### EC-002: Very Long Path Parameters

| Field | Value |
|-------|-------|
| **ID** | EC-002 |
| **Title** | Handle excessively long path parameters gracefully |
| **Category** | Edge Case/Performance |
| **Priority** | P3 |
| **Preconditions** | Application running, proxy configured |
| **Steps** | 1. Send request with very long report ID or parameter<br>2. Veify no buffer overflow or memory issues occur<br>3. Check response handling for oversized payloads |
| **Expected Result** | Request processed normally; large responses handled by upstream timeout settings |
| **Actual Result** | |
| **Status** | |

### EC-003: Empty Response from Upstream

| Field | Value |
|-------|-------|
| **ID** | EC-003 |
| **Title** | Handle empty responses from upstream server |
| **Category** | Edge Case/Error Handling |
| **Priority** | P2 |
| **Preconditions** | Application runnstream configured to return 204 No Content |
| **Steps** | 1. Configure upstream endpoint that returns no content<br>2. Send request and verify response handling<br>3. Check that empty responses don't cause null pointer exceptions |
| **Expected Result** | Empty responses handled gracefully; appropriate status code returned to client |
| **Actual Result** | |
| **Status** | |

### EC-004: Unicode Path Handling

| Field | Value |
|-------|-------|
| **ID** | EC-004 |
| **Title** | Handle paths with non-ASCIIharacters correctly |
| **Category** | Edge Case/Internationalization |
| **Priority** | P3 |
| **Preconditions** | Application running, proxy configured for internationalized URLs |
| **Steps** | 1. Send request with Unicode path segments<br>2. Verify proper UTF-8 encoding and decoding throughout flow<br>3. Check that Jsoup handles unicode correctly in HTML parsing |
| **Expected Result** | Unicode characters preserved through the entire chain; no mojibake or corruption |
| **Actual Result** | |
| **Status** | |

---

## Regression Test Cases (After Updates)

### REG-001: Header Filtering Consistency After Update

| Field | Value |
|-------|-------|
| **ID** | REG-001 |
| **Title** | Verify header filtering still works after documentation updates |
| **Category** | Regression/Security |
| **Priority** | P2 |
| **Preconditions** | Application running, proxy configured with current settings |
| **Steps** | 1. Send request to proxied endpoint<br>2. Inspect response headers for hop-by-hop and server info leaks<b3. Verify all expected headers are filtered correctly |
| **Expected Result** | All hop-by-hop headers removed; no sensitive version information leaked in responses |
| **Actual Result** | |
| **Status** | |

### REG-002: Security Policy Validation After Update

| Field | Value |
|-------|-------|
| **ID** | REG-002 |
| **Title** | Verify security policy still blocks unauthorized resources |
| **Category** | Regression/Security |
| **Priority** | P1 |
| **Preconditions** | Application running, proxy configuth current settings |
| **Steps** | 1. Attempt to access blocked admin endpoints<br>2. Try accessing metadata endpoint<br>3. Verify all security policies still enforced correctly |
| **Expected Result** | All previously identified vulnerabilities remain patched; no new attack vectors introduced |
| **Actual Result** | |
| **Status** | |



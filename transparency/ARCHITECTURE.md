# Architecture Documentation

## System Overview

**Project Name:** efinance-powerbi  
**Description:** Secure Spring Boot proxy for embedded Power BI Report Server reports with transparency dashboard capabilities.

## System Design

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Power BI Proxy Application                     │
│                        (Spring Boot 3.3.5)                        │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   Controllers │  │    Services  │  │      Configurations   │  │
│  │               │  │              │  │                        │  │
│  │ - ProxyController │ - ProxyService │ - HttpClientConfig    │  │
│  │ - FaviconController │ - SecurityConfig │ - PowerBiReportProperties │
│  │ - RedirectController │ - StartupConfigurationLogger │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              GlobalExceptionHandler                        │   │
│  │  (Centralized Exception Handling)                          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Power BI Report Server                        │
│              (Upstream Report Server)                            │
└─────────────────────────────────────────────────────────────────┘
```

## Modules

### 1. Controllers Module (`/web`)

| Controller | Purpose | Endpoints |
|------------|---------|-----------|
| `PowerBiProxyController` | Main proxy controller for report access | `/reports/powerbi/transparency`, `/reports/powerbi/proxy/**` |
| `FaviconController` | Handles favicon and dev tools metadata | `/favicon.ico`, `/.well-known/appspecific/com.chrome.devtools.json` |
| `PowerBiViewerRedirectController` | Redirects various Power BI paths to proxy | `/powerbi/**`, `/PowerBI/**`, `/ReportServer/**`, etc. |

### 2. Services Module (`/service`)

| Service | Purpose |
|---------|---------|
| `PowerBiProxyService` | Core service for proxying requests to Power BI Report Server, handling authentication, headers, and response wrapping |

### 3. Configurations Module (`/config`)

| Configuration | Purpose |
|---------------|---------|
| `HttpClientConfig` | Configures Apache HttpClient5 with connection pooling, timeouts, and NTLM/BASIC authentication |
| `PowerBiReportProperties` | Configuration properties for Power BI Report Server connection (baseUrl, auth, timeouts, allowed paths) |
| `SecurityConfig` | Spring Security configuration with CSRF exemptions, CSP headers, and authentication rules |
| `StartupConfigurationLogger` | Logs application startup configuration for audit purposes |

### 4. Exceptions Module (`/web`)

| Exception | Purpose |
|-----------|---------|
| `ForbiddenProxyRequestException` | Custom exception for forbidden proxy requests |
| `ReportServerUnavailableException` | Custom exception when upstream server is unavailable |
| `GlobalExceptionHandler` | Centralized exception handler returning standardized ProblemDetail responses |

## Dependencies

### Build Tool
- **Maven** (parent: `spring-boot-starter-parent` v3.3.5)

### Core Dependencies
| Dependency | Version/Source | Purpose |
|------------|----------------|---------|
| `spring-boot-starter-web` | 3.3.5 | Web MVC framework |
| `spring-boot-starter-security` | 3.3.5 | Security configuration |
| `spring-boot-starter-validation` | 3.3.5 | Bean validation |
| `spring-boot-starter-actuator` | 3.3.5 | Health checks and monitoring |
| `httpclient5` | Apache | HTTP client for proxying |
| `jsoup` | 1.18.1 | HTML parsing for transparency report |

## Data Flow

### Request Flow (Proxy Request)

```
1. Client Request → PowerBiViewerRedirectController
2. Controller → PowerBiProxyService.proxyPath()
3. Service → HttpClient (with auth) → Power BI Report Server
4. Response → Headers filtered → Client Response
```

### Transparency Report Flow

```
1. Client → /reports/powerbi/transparency
2. PowerBiProxyController.openTransparencyReport()
3. PowerBiProxyService.openTransparencyReport()
4. Generate HTML with iframe to transparency report URL
5. Return HTML response to client
```

### Authentication Flow

```
1. Client → Proxy (with credentials)
2. Proxy → Power BI Report Server (with auth)
3. Server → Response with PBIRS_ cookies
4. Proxy → Forward cookies to client (filtered)
```

## Security Architecture

### Security Filter Chain
- **CSRF Protection**: Enabled with exemptions for Power BI paths
- **CSP Headers**: Restricts sources to 'self' with exceptions for data/blob
- **X-Frame-Options**: SAMEORIGIN to prevent clickjacking
- **Authentication**: Optional (disabled by default for local testing)
- **Authorization**: All requests require authentication when enabled

### Allowed Resource Paths
- `/Reports/`
- `/ReportServer/`
- `/Reserved.ReportViewerWebControl.axd`
- `/PowerBI/`
- `/powerbi/`
- `/api/`
- `/explore/`
- `/modelsAndExploration/`
- `/querydata/`
- `/metadata/`
- `/resources/`
- `/public/`

## Connection Configuration

### HttpClient Settings
- **Connection Pool**: 20 total, 10 per route
- **Connect Timeout**: 5 seconds (configurable)
- **Response Timeout**: 60 seconds (configurable)
- **Keep-Alive**: Enabled with 5-minute idle eviction
- **Auth Types**: BASIC, NTLM, NONE

### NTLM Specific
- Connection manager shared: false (for auth-state persistence)
- Evict expired connections
- Multi-step handshake support

## File Structure

```
src/main/java/eg/com/efinance/powerbi/
├── EfinancePowerBiApplication.java          # Main application entry
├── config/
│   ├── HttpClientConfig.java                # HTTP client configuration
│   ├── PowerBiReportProperties.java         # Configuration properties
│   ├── StartupConfigurationLogger.java      # Startup logging
│   └── SecurityConfig.java                  # Security configuration
├── service/
│   └── PowerBiProxyService.java             # Core proxy service
└── web/
    ├── PowerBiProxyController.java          # Main controller
    ├── FaviconController.java               # Favicon handler
    ├── PowerBiViewerRedirectController.java # Redirect controller
    ├── ForbiddenProxyRequestException.java  # Custom exception
    ├── ReportServerUnavailableException.java # Custom exception
    └── GlobalExceptionHandler.java           # Exception handler
```

## Deployment

### Docker
- Dockerfile provided for containerized deployment
- Uses multi-stage build for optimized image size

### Actuator Endpoints
- `/actuator/health/**` - Health check endpoint (public)
- `/actuator/info` - Application info
- `/actuator/beans` - Bean information


## Additional Components

### 5. Services Module - Detailed

#### PowerBiProxyService
- **Purpose:** Core service for proxying requests to Power BI Report Server
- **Key Responsibilities:**
  - HTTP request forwarding with authentication
  - Header filtering (hop-by-hop and response headers)
  - Response wrapping with HTML iframe for transparency reports
  - Cookie handling for session persistence
  - Path validation via ProxyPathSecurityPolicy
  - JSON sanitization via SensitiveJsonSanitizer

#### ProxyPathSecurityPolicy
- **Purpose:** Security policy for validating proxy paths
- **Blocked Resources:**
  - OData metadata endpoints (`/$metadata`)
  - Administrative resources (AlertSubscriptions, CatalogItems, DataSources, Schedules, Subscriptions, System, Telemetry, Usersettings)
  - Enumerable resources without parameters (CatalogItems, PowerBIReports, Reports)
  - Browse and home folder paths
- **Allowed Resources:**
  - Keyed report resources (e.g., `/api/v2.0/PowerBIReports(123)`)
  - Static assets (JS, CSS)
  - Specific report paths configured in properties

#### SensitiveJsonSanitizer
- **Purpose:** Remove sensitive fields from JSON responses
- **Sensitive Fields Removed:**
  - `createdby`, `modifiedby`, `createddate`, `modifieddate`
  - `creator`, `modifier`, `createdbyusername`, `modifiedbyusername`
- **Implementation:** Recursive removal from JSON objects and arrays

### 6. Exceptions Module - Detailed

#### ForbiddenProxyRequestException
- **Purpose:** Custom exception thrown when proxy path is not allowed
- **Usage:** Thrown by ProxyPathSecurityPolicy.validate() when access is denied

#### ReportServerUnavailableException
- **Purpose:** Custom exception when upstream Power BI Report Server is unavailable
- **Usage:** Thrown when HTTP client encounters server errors

#### GlobalExceptionHandler
- **Purpose:** Centralized exception handling across all controllers
- **Handled Exceptions:**
  - ForbiddenProxyRequestException → 403 FORBIDDEN
  - AccessDeniedException → 403 FORBIDDEN
  - ReportServerUnavailableException → 502 BAD_GATEWAY
  - NoResourceFoundException → 404 NOT_FOUND
  - Exception (catch-all) → 500 INTERNAL_SERVER_ERROR
- **Response Format:** ProblemDetail with appropriate HTTP status

## Security Architecture

### Authentication Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Client Request                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              SecurityConfig (Filter Chain)                    │
│  - Check app.security.enabled flag                            │
│  - If enabled: require authentication                         │
│  - If disabled: permit all (local testing mode)               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              HttpClientConfig (Credentials)                   │
│  - BASIC: Username/Password credentials                       │
│  - NTLM: Domain/Workstation/Username credentials              │
│  - NONE: No authentication (public access)                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              PowerBiProxyService (Proxy Request)             │
│  - Forward request to Power BI Report Server                  │
│  - Filter hop-by-hop headers                                  │
│  - Filter response headers                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              Power BI Report Server                           │
└─────────────────────────────────────────────────────────────┘
```

### Security Headers

| Header | Value | Purpose |
|--------|-------|---------|
| Content-Security-Policy | default-src 'self'; frame-src 'self'; ... | Restrict resource loading |
| X-Frame-Options | SAMEORIGIN | Prevent clickjacking |
| Strict-Transport-Security | Enabled via HSTS | Force HTTPS |

### CSRF Protection

- Enabled by default
- Ignored for Power BI proxy paths to prevent interference with upstream cookies
- Cookie CSRF token with HTTPOnly=false for JavaScript access

## Data Flow Diagrams

### Complete Request Lifecycle

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐
│   Client    │────▶│  Controller  │────▶│   PowerBiProxyService│
└─────────────┘     └──────────────┘     └─────────────────────┘
                                              │
                                              ▼
┌─────────────────────┐     ┌─────────────────────────────────┐
│   PowerBiReport     │────▶│   HttpClient (with Auth)         │
│   Properties        │     └─────────────────────────────────┘
└─────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────┐     ┌─────────────────────┐
│   Power BI Report Server        │◀────│   Response Headers   │
└─────────────────────────────────┘     └─────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────┐     ┌─────────────────────┐
│   SensitiveJsonSanitizer        │────▶│   Filtered Response  │
└─────────────────────────────────┘     └─────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────┐     ┌─────────────────────┐
│   ProxyPathSecurityPolicy       │────▶│   Validated Response │
└─────────────────────────────────┘     └─────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────┐     ┌─────────────────────┐
│   GlobalExceptionHandler         │────▶│   Final Response     │
└─────────────────────────────────┘     └─────────────────────┘
```

## Deployment Architecture

### Docker Deployment

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Container                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Spring Boot Application (JRE 17)                     │  │
│  │  - efinance-powerbi-0.0.1-SNAPSHOT.jar                │  │
│  │  - Embedded Tomcat                                     │  │
│  │  - Spring Security                                     │  │
│  │  - Apache HttpClient5                                  │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Configuration Files                                   │  │
│  │  - application.properties                              │  │
│  │  - powerbi.report-server.*                             │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Monitoring and Observability

### Actuator Endpoints

| Endpoint | Purpose | Access |
|----------|---------|--------|
| `/actuator/health` | Health check | Public |
| `/actuator/info` | Application info | Public |
| `/actuator/env` | Environment variables | Public |

### Audit Logging

- **AUDIT_POWERBI_REPORT_ACCESS:** Logs transparency report access
- **StartupConfigurationLogger:** Logs application startup configuration
- **GlobalExceptionHandler:** Logs unexpected errors

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.0.1-SNAPSHOT | 2024 | Initial release |
| - | - | Documentation updates |


## Additional Components

### 5. Exceptions Module (`/web`)

| Exception | Purpose | Handler Method | HTTP Status |
|-----------|---------|----------------|-------------|
| `ForbiddenProxyRequestException` | Unauthorized API resource access | handleForbiddenProxyRequest() | 403 FORBIDDEN |
| `ReportServerUnavailableException` | Upstream server unreachable | handleReportServerUnavailable() | 502 BAD_GATEWAY |
| `AccessDeniedException` | Spring Security authorization failure | handleAccessDenied() | 403 FORBIDDEN |
| `NoResourceFoundException` | Resource not found on upstream | handleNoResourceFound() | 404 NOT_FOUND |

### Exception Response Format (ProblemDetail)

```json
{
  "type": "about:blank",
  "title": "<status>",
  "detail": "<error message>"
}
```

## Security Architecture

### Authentication Flow

1. **Client Request** → Spring Security Filter Chain
2. **Authorization Check**: 
   - If `app.security.enabled=false`: Permit all (local testing)
   - If enabled: Require authenticated user
3. **CSRF Protection**: Enabled with exemptions for proxy paths
4. **CSP Headers**: Enforced via Content-Security-Policy

### Header Filtering Strategy

**Hop-by-Hop Headers Removed:** Connection, Keep-Alive, Proxy-Authorize, etc.  
**Response Headers Skipped:** Server version info, ASP.NET versions, ReportServer headers  

## Data Flow Diagrams

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Client    │────▶│ PowerBiProxyApp  │────▶│Power BI RS        │
│             │     │                  │     │                   │
│ Request + Auth         Proxy Service          Report Server      │
└─────────────┘     │ (Auth, Headers)       ┌──────────────────┘
                    │                       │
                    ▼                       ▼
              Filtered Response        Transparency Report HTML
```

## Module Dependencies Graph

```
PowerBiProxyService
    ├── HttpClientConfig → Apache HttpClient5 + Connection Pooling
    ├── PowerBiReportProperties → Configuration Binding
    └── ProxyPathSecurityPolicy → Path Validation Rules
    
Controllers:
├── PowerBiProxyController → Main proxy endpoint handler
├── FaviconController → Static resource handlers (204 No Content)
└── PowerBiViewerRedirectController → URL pattern redirects

Configurations:
├── SecurityConfig → Spring Security filter chain, CSP headers
├── StartupConfigurationLogger → Application startup audit logging
```

## Design Patterns Used

1. **Proxy Pattern**: Core proxying of upstream requests with header filtering
2. **Strategy Pattern**: Authentication type selection (BASIC/NTLM/NONE)
3. **Template Method**: Standardized exception handling via @RestControllerAdvice
4. **Dependency Injection**: Spring IoC for service and configuration beans



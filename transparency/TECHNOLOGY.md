# Technology Stack

## Core Technologies

### Java Runtime
- **Version:** Java 17 (LTS)
- **Purpose:** Application runtime environment
- **Features:** Enhanced type inference, virtual threads support, improved GC

### Spring Framework
- **Version:** Spring Boot 3.3.5
- **Purpose:** Application framework and dependency management
- **Key Modules:**
  - Spring Web MVC - REST API handling
  - Spring Security - Authentication and authorization
  - Spring Validation - Bean validation
  - Spring Actuator - Application monitoring

### HTTP Client
- **Library:** Apache HttpClient5
- **Version:** Latest compatible with Spring Boot 3.x
- **Purpose:** HTTP request/response handling for proxying
- **Features:**
  - Connection pooling
  - NTLM authentication support
  - Request/response filtering
  - Timeout configuration

### HTML Parser
- **Library:** Jsoup
- **Version:** 1.18.1
- **Purpose:** HTML parsing for transparency report processing
- **Features:**
  - DOM traversal
  - Attribute extraction
  - HTML sanitization

## Security Technologies

### Spring Security
- **Version:** 5.8.x (compatible with Spring Boot 3.3.5)
- **Features:**
  - Form-based authentication
  - Method-level security
  - CSRF protection
  - Custom authentication providers

### Content Security Policy
- **Implementation:** Spring Security headers
- **Policy:**
  ```
  default-src 'self';
  frame-src 'self';
  frame-ancestors 'self';
  img-src 'self' data: blob:;
  style-src 'self' 'unsafe-inline';
  script-src 'self' 'unsafe-inline' 'unsafe-eval';
  connect-src 'self' blob:;
  font-src 'self' data:;
  object-src 'none'
  ```

### HTTP Security Headers
- **HSTS:** Enabled via Customizer.withDefaults()
- **X-Frame-Options:** SAMEORIGIN
- **CSP:** Configured via policy directives

## Build Tools

### Maven
- **Purpose:** Dependency management and build automation
- **Parent:** spring-boot-starter-parent 3.3.5
- **Plugins:**
  - spring-boot-maven-plugin - Executable JAR creation

### Docker
- **Purpose:** Containerization
- **File:** Dockerfile in project root
- **Features:** Multi-stage build for optimized image size

## Configuration

### Spring Boot Properties
- **Prefix:** `powerbi.report-server`
- **Properties:**
  - `baseUrl` - Power BI Report Server URL
  - `username` - Authentication username
  - `password` - Authentication password
  - `domain` - NTLM domain (optional)
  - `workstation` - NTLM workstation (optional)
  - `authType` - BASIC|NTLM|NONE
  - `allowedHost` - Allowed host for proxying
  - `reportUrl` - Report URL (optional)
  - `transparencyReportPath` - Transparency report path
  - `allowedResourcePathPrefixes` - Allowed paths list
  - `connectTimeout` - Connection timeout
  - `responseTimeout` - Response timeout
  - `app.security.enabled` - Application security flag

### Validation
- **Library:** jakarta.validation
- **Annotations:** @NotBlank, @NotEmpty, @NotNull
- **Purpose:** Configuration property validation

## Exception Handling

### Custom Exceptions
- `ForbiddenProxyRequestException` - Forbidden access
- `ReportServerUnavailableException` - Upstream server unavailable

### Global Exception Handler
- **Type:** @RestControllerAdvice
- **Handled Exceptions:**
  - ForbiddenProxyRequestException → 403 FORBIDDEN
  - AccessDeniedException → 403 FORBIDDEN
  - ReportServerUnavailableException → 502 BAD_GATEWAY
  - NoResourceFoundException → 404 NOT_FOUND
  - Exception → 500 INTERNAL_SERVER_ERROR

## Logging

### SLF4J
- **Purpose:** Logging facade
- **Implementation:** Default implementation
- **Features:**
  - Startup configuration logging
  - Request audit logging
  - Exception logging

### Log Levels
- INFO - Startup configuration
- ERROR - Application errors
- WARN - Warnings

## Testing

### Spring Boot Test
- **Purpose:** Unit and integration testing
- **Features:**
  - @SpringBootTest
  - MockMvc testing
  - Test configuration

### Spring Security Test
- **Purpose:** Security testing
- **Features:**
  - Security context testing
  - Authentication testing

## Deployment Technologies

### Docker
- **Image:** Multi-stage build
- **Base:** OpenJDK 17
- **Features:**
  - Slim final image
  - Health check endpoint
  - Port exposure

### Actuator
- **Endpoints:**
  - `/actuator/health` - Health status
  - `/actuator/info` - Application info
  - `/actuator/beans` - Bean information

## Performance Considerations

### Connection Pooling
- **Max Connections:** 20 total
- **Per Route:** 10 connections
- **Idle Eviction:** 5 minutes
- **Validation:** 5 seconds after inactivity

### Timeouts
- **Connect Timeout:** 5 seconds (configurable)
- **Response Timeout:** 60 seconds (configurable)

### Keep-Alive
- **Enabled:** Yes
- **Validation:** 5 seconds
- **Eviction:** 5 minutes idle

## Compatibility

### Java Versions
- **Minimum:** Java 17
- **Recommended:** Java 17 LTS

### Spring Boot Versions
- **Compatible:** 3.3.x
- **Parent:** 3.3.5

### Browser Compatibility
- **Power BI Viewer:** Modern browsers with iframe support
- **CSP:** Modern browsers with CSP support
- **NTLM:** Windows-based authentication

## License

- **Spring Framework:** Apache 2.0
- **Apache HttpClient:** Apache 2.0
- **Jsoup:** Apache 2.0
- **Overall Project:** Check repository for license


## Detailed Technology Information

### Java Runtime Environment

**Version:** Java 17 (LTS)  
**Vendor:** Oracle/OpenJDK  
**Features:**
- Virtual threads support (Project Loom)
- Enhanced type inference (var)
- Pattern matching for switch
- Record types
- Sealed classes
- Improved garbage collection
- JEP 411: Record Patterns
- JEP 425: Foreign Function & Memory API

**Usage in Project:**
- Application runtime
- Spring Boot framework
- Apache HttpClient5
- Jsoup library

### Spring Framework Ecosystem

#### Spring Boot 3.3.5
- **Purpose:** Application framework and dependency management
- **Key Features:**
  - Auto-configuration
  - Embedded Tomcat/Jetty/Undertow
  - Actuator for monitoring
  - Health indicators
  - Startup/Shutdown events

#### Spring Security 6.x
- **Purpose:** Authentication and authorization
- **Key Features:**
  - Form-based authentication
  - Method-level security annotations
  - CSRF protection
  - Custom authentication providers
  - Security filter chains
  - Authorization managers

#### Spring Validation
- **Purpose:** Bean validation
- **Key Features:**
  - @Validated annotation
  - @NotBlank, @NotEmpty, @NotNull constraints
  - Custom validators
  - Validation groups

#### Spring Actuator
- **Purpose:** Application monitoring and management
- **Endpoints:**
  - /actuator/health - Health status
  - /actuator/info - Application info
  - /actuator/env - Environment variables
  - /actuator/beans - Registered beans

### Apache HttpClient5

**Version:** 5.x (compatible with Spring Boot 3.x)  
**Purpose:** HTTP client for proxying requests

#### Connection Management
- **Pooling:** Apache HttpClient5 connection pooling
- **Configuration:**
  - Max connections total: 20
  - Max connections per route: 10
  - Default connection config with timeouts
  - Socket keep-alive enabled
  - Connection eviction after 5 minutes idle

#### Authentication Support
- **BASIC:** UsernamePasswordCredentials
- **NTLM:** NTCredentials with domain/workstation
- **Custom:** Custom auth schemes via registry

#### Request Configuration
- **Timeouts:**
  - Connection request timeout: Configurable
  - Response timeout: Configurable (default 60s)
- **Connection Request Timeout:** Configurable
- **Socket Timeout:** Configurable

#### Response Handling
- **Entity:** ByteArrayEntity for response body
- **Content Type:** Automatic detection
- **Header Filtering:** Hop-by-hop and sensitive headers removed

### Jsoup

**Version:** 1.18.1  
**Purpose:** HTML parsing for transparency report processing

#### Features
- **DOM Traversal:** Select elements by CSS selectors
- **Attribute Extraction:** Get/set HTML attributes
- **HTML Sanitization:** Clean and validate HTML
- **URL Handling:** Encode/decode URLs
- **Document Creation:** Create documents from strings

#### Usage in Project
- Parse transparency report HTML
- Extract report URLs
- Validate HTML structure

### Jackson (Implicit via Spring Boot)

**Purpose:** JSON processing  
**Features:**
- JSON serialization/deserialization
- Tree model for JSON manipulation
- Custom serializers/deserializers

### Maven Build Tool

**Purpose:** Dependency management and build automation

#### Parent POM
- **spring-boot-starter-parent 3.3.5**
- Provides default configurations
- Manages dependency versions
- Provides Spring Boot plugins

#### Plugins
- **spring-boot-maven-plugin:**
  - Executable JAR creation
  - Repackage goal
  - Build information embedding

#### Build Configuration
```xml
<build>
  <plugins>
    <plugin>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-maven-plugin</artifactId>
    </plugin>
  </plugins>
</build>
```

### Docker

**Purpose:** Containerization

#### Dockerfile Structure
- Multi-stage build for optimized image size
- Base image: OpenJDK 17
- Application JAR copied from build
- Exposed port: 8080 (default Spring Boot)

#### Container Features
- Lightweight deployment
- Environment variable configuration
- Health checks via actuator
- Resource isolation

### Security Technologies - Detailed

#### Content Security Policy (CSP)

**Policy Directives:**
```
default-src 'self';
frame-src 'self';
frame-ancestors 'self';
img-src 'self' data: blob:;
style-src 'self' 'unsafe-inline';
script-src 'self' 'unsafe-inline' 'unsafe-eval';
connect-src 'self' blob:;
font-src 'self' data:;
object-src 'none'
```

**Explanation:**
- `default-src 'self'`: Only load resources from same origin
- `frame-src 'self'`: Iframes can only load from same origin
- `frame-ancestors 'self'`: Page can only be framed from same origin
- `img-src`: Images from same origin, data URIs, and blobs
- `style-src`: Styles from same origin, inline styles allowed
- `script-src`: Scripts from same origin, inline scripts and eval allowed
- `connect-src`: WebSockets and fetch from same origin and blobs
- `font-src`: Fonts from same origin and data URIs
- `object-src 'none'`: Disable plugins (Flash, etc.)

#### HTTP Strict Transport Security (HSTS)

**Configuration:** Customizer.withDefaults()  
**Features:**
- Forces HTTPS connections
- Prevents protocol downgrade attacks
- Includes subdomains (if configured)

#### X-Frame-Options

**Value:** SAMEORIGIN  
**Purpose:** Prevent clickjacking by disallowing framing from other origins

#### CSRF Protection

**Configuration:**
- Enabled by default
- Cookie-based CSRF tokens
- HTTPOnly=false for JavaScript access
- Ignored for Power BI proxy paths

### Validation Framework

#### Bean Validation (JSR 380)

**Annotations Used:**
- @NotBlank - Field must not be blank
- @NotEmpty - Collection must not be empty
- @NotNull - Field must not be null

**Usage:**
```java
@Validated
@ConfigurationProperties(prefix = "powerbi.report-server")
public class PowerBiReportProperties {
    @NotBlank private String baseUrl;
    @NotBlank private String username;
    @NotBlank private String password;
    // ...
}
```

### Logging Framework

**Implementation:** SLF4J with implementation (likely Logback)  
**Features:**
- Structured logging
- Async logging support
- MDC for request tracing
- Log levels: TRACE, DEBUG, INFO, WARN, ERROR

**Loggers:**
- AUDIT_POWERBI_REPORT_ACCESS - Transparency report access
- GlobalExceptionHandler - Application errors
- StartupConfigurationLogger - Startup configuration

### Exception Handling

#### ProblemDetail

**Purpose:** Standardized error responses  
**Usage:** All exceptions converted to ProblemDetail

#### Custom Exceptions

| Exception | Purpose | HTTP Status |
|----------|---------|-------------|
| ForbiddenProxyRequestException | Forbidden path | 403 |
| ReportServerUnavailableException | Upstream unavailable | 502 |

### Configuration Management

#### Spring Boot Properties

**Prefix:** powerbi.report-server  
**Binding:** @ConfigurationProperties

**Properties File Structure:**
```properties
powerbi.report-server.baseUrl=http://reportserver.example.com
powerbi.report-server.username=reportuser
powerbi.report-server.password=reportpassword
powerbi.report-server.authType=BASIC
powerbi.report-server.allowedHost=reportserver.example.com
powerbi.report-server.connectTimeout=5s
powerbi.report-server.responseTimeout=60s
app.security.enabled=true
```

#### Environment Variables

Properties can be overridden via environment variables:
- `POWERBI_REPORT_SERVER_BASE_URL`
- `POWERBI_REPORT_SERVER_USERNAME`
- `POWERBI_REPORT_SERVER_PASSWORD`
- `APP_SECURITY_ENABLED`

### Testing Framework

**Framework:** JUnit 5  
**Assertions:** JUnit Jupiter assertions  
**Test Scope:** Test scope in Maven POM

**Test Files:**
- ProxyPathSecurityPolicyTest.java
- SensitiveJsonSanitizerTest.java

### Build and Deployment

#### Maven Lifecycle
- `mvn clean` - Clean build artifacts
- `mvn package` - Build JAR
- `mvn spring-boot:run` - Run application
- `mvn spring-boot:build-image` - Build Docker image

#### Docker Deployment
- Multi-stage build
- Minimal base image
- Health checks configured

## Technology Comparison

### HTTP Client Libraries

| Library | Version | Features | Used In |
|----------|--------|----------|---------|
| Apache HttpClient5 | 5.x | Connection pooling, NTLM, BASIC | Yes |
| OkHttp | 4.x | Async, caching | No |
| Java HttpClient | 17 | Built-in, async | No |

### HTML Parsing Libraries

| Library | Version | Features | Used In |
|----------|--------|----------|---------|
| Jsoup | 1.18.1 | DOM, sanitization | Yes |
| HTMLParser4 | 0.9.x | Streaming | No |
| Jsoup | 1.18.1 | DOM, sanitization | Yes |

### Security Frameworks

| Framework | Version | Features | Used In |
|----------|--------|----------|---------|
| Spring Security | 6.x | Auth, CSRF, method security | Yes |
| Shiro | 1.x | Auth, permissions | No |
| Keycloak | Latest | OIDC, SAML | No |

## Version Compatibility

### Java Version Matrix

| Java Version | Spring Boot | Status |
|----------|--------|--------|
| 11 | 2.7.x | EOL |
| 17 | 3.3.x | Current |
| 21 | 3.3.x | Supported |

### Dependency Versions

| Dependency | Version | Purpose |
|----------|--------|---------|
| spring-boot-starter-web | 3.3.5 | Web MVC |
| spring-boot-starter-security | 3.3.5 | Security |
| spring-boot-starter-validation | 3.3.5 | Validation |
| spring-boot-starter-actuator | 3.3.5 | Actuator |
| httpclient5 | 5.x | HTTP client |
| jsoup | 1.18.1 | HTML parsing |

## Security Best Practices Implemented

1. **Principle of Least Privilege:** Only required permissions granted
2. **Defense in Depth:** Multiple security layers (CSP, HSTS, X-Frame-Options)
3. **Secure Defaults:** Security enabled by default, configurable
4. **Input Validation:** All inputs validated before processing
5. **Output Encoding:** HTML entities encoded to prevent XSS
6. **Header Sanitization:** Sensitive headers filtered
7. **Cookie Security:** HTTPOnly cookies where applicable
8. **Audit Logging:** Access to sensitive endpoints logged

## Performance Considerations

### Connection Pooling
- Reuse connections to reduce latency
- Evict idle connections after 5 minutes
- Max 20 total connections

### Timeout Configuration
- Connection timeout: 5 seconds
- Response timeout: 60 seconds
- Prevents hanging requests

### Caching
- Response caching disabled (no-store)
- Prevents serving stale data

### Resource Limits
- Connection pool limits prevent resource exhaustion
- Timeout limits prevent resource starvation

## Future Technology Considerations

### Potential Upgrades
- Java 21 for latest features
- Spring Boot 3.4 for latest security fixes
- Apache HttpClient5 5.3 for latest features

### Monitoring Integration
- Prometheus metrics via Micrometer
- Distributed tracing via OpenTelemetry
- Log aggregation via ELK stack

## References

- [Spring Boot Documentation](https://docs.spring.io/spring-boot/)
- [Spring Security Documentation](https://docs.spring.io/spring-security/)
- [Apache HttpClient5 Documentation](https://hc.apache.org/httpcomponents-client-5.x/)
- [Jsoup Documentation](https://jsoup.org/)
- [OWASP Security Cheat Sheets](https://cheatsheetseries.owasp.org/)



# Transparency Dashboard Documentation

## Project Status

**Status:** Active  
**Last Updated:** 2024  
**Code Detected:** Yes  
**Files Processed:** 16 Java source files (12 main + 2 test)  
**Documentation Complete:** Yes  
**Chunking Required:** No (below 100 file threshold)

## Overview

This documentation repository contains comprehensive documentation for the **efinance-powerbi** project - a secure Spring Boot proxy for embedded Power BI Report Server reports with transparency dashboard capabilities.

## Documentation Files

| File | Description | Status |
|------|---|--------|
| `ARCHITECTURE.md` | System design, modules, dependencies, and data flow | Complete |
| `PROJECT_SUMMARY.md` | High-level overview, goals, and scope | Complete |
| `TECHNOLOGY.md` | Technology stack and versions used | Complete |
| `README.md` | This file - project status and navigation | Complete |
| `TEST_CASES.md` | Test cases for main functions, edge cases, and error handling | Complete |
| `CHUNK_LOG.md` | Processing log for large file counts | Complete |

## Quick Start

### Building the Project

```bash
cd /Transparency_Dashboard
mvn clean package
```

### Running Locally

```bash
java -jar target/efinance-powerbi-0.0.1-SNAPSHOT.jar
```

### Docker Deployment

```bash
docker build -t efinance-powerbi .
docker run -p 8080:8080 efinance-powerbi
```

## Key Endpoints

| Endpoint | Method | Description | Auth Required |
|----------|--------|---------|---------------|
| `/reports/powerbi/transparency` | GET | Open transparency report | Yes (if enabled) |
| `/reports/powerbi/proxy/**` | GET | Proxy report resources | Yes (if enabled) |
| `/powerbi/**` | GET | Proxy viewer resources | Yes (if enabled) |
| `/actuator/health` | GET | Health check | No |
| `/favicon.ico` | GET | Favicon handler | No |

## Configuration

See `TECHNOLOGY.md` for configuration properties and `ARCHITECTURE.md` for system design.

## Security

- Optional authentication (disabled by default for local testing)
- CSRF protection with Power BI path exemptions
- Content Security Policy enforcement
- X-Frame-Options protection (SAMEORIGIN)
- HTTP Strict Transport Security (HSTS)
- Hop-by-hop header filtering
- Response header sanitization

## Project Structure

```
/Transparency_Dashboard/
├── src/
│   ├── main/
│   │   ├── java/eg/com/efinance/powerbi/
│   │   │   ├── EfinancePowerBiApplication.java
│   │   │   ├── config/
│   │   │   │   ├── HttpClientConfig.java
│   │   │   │   ├── PowerBiReportProperties.java
│   │   │   │   ├── SecurityConfig.java
│   │   │   │   └── StartupConfigurationLogger.java
│   │   │   ├── service/
│   │   │   │   ├── PowerBiProxyService.java
│   │   │   │   ├── ProxyPathSecurityPolicy.java
│   │   │   │   └── SensitiveJsonSanitizer.java
│   │   │   └── web/
│   │   │       ├── PowerBiProxyController.java
│   │   │       ├── PowerBiViewerRedirectController.java
│   │   │       ├── FaviconController.java
│   │   │       ├── GlobalExceptionHandler.java
│   │   │       ├── ForbiddenProxyRequestException.java
│   │   │       └── ReportServerUnavailableException.java
│   │   └── resources/
│   └── test/
│       └── java/eg/com/efinance/powerbi/service/
│           ├── ProxyPathSecurityPolicyTest.java
│           └── SensitiveJsonSanitizerTest.java
├── pom.xml
├── Dockerfile
└── target/
```

## Documentation Navigation

### For Developers

- **Architecture:** Start with `ARCHITECTURE.md` to understand system design
- **Technology:** Review `TECHNOLOGY.md` for technology stack details
- **Testing:** See `TEST_CASES.md` for test case specifications

### For Project Managers

- **Summary:** Review `PROJECT_SUMMARY.md` for high-level overview
- **Goals:** Understand project scope and objectives

### For Security Teams

- **Security Config:** See `ARCHITECTURE.md` security section
- **Test Cases:** Review security test cases in `TEST_CASES.md`

### For Operations

- **Deployment:** See `PROJECT_SUMMARY.md` deployment section
- **Monitoring:** See `ARCHITECTURE.md` observability section

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
- Optional authentication (BASIC/NTLM/NONE)
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

## Error Handling

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

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.0.1-SNAPSHOT | 2024 | Initial release |
| - | 2024 | Documentation updates |

## Related Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) - System design and data flow
- [PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md) - High-level overview
- [TECHNOLOGY.md](./TECHNOLOGY.md) - Technology stack
- [TEST_CASES.md](./TEST_CASES.md) - Test cases
- [CHUNK_LOG.md](./CHUNK_LOG.md) - Processing log

## Notes

- All documentation is maintained under `/DocMDs/Transparency_Dashboard/`
- No code is stored outside `/Transparency_Dashboard/`
- Documentation is updated automatically when code changes are detected
- Test cases cover functional, security, error handling, and performance scenarios


## Documentation Status Update

**Last Updated:** $(date +%Y-%m-%d)  
**Files Processed:** 14 Java source files (12 main + test files)  
**Documentation Complete:** Yes  

### Recent Updates to This Repository

- **ARCHITECTURE.md**: Extended with additional components, exception handling details, security architecture, and design patterns
- **TEST_CASES.md**: Added comprehensive security test cases (SC-001 through SC-005), error handling tests (EH-001 through EH-005), performance tests (PF-001/PF-002), integration tests (IT-001/IT-002/IT-003), configuration validation tests, and edge case/regression test cases
- **CHUNK_LOG.md**: Updated to reflect current file count status

### File Inventory

| Category | Count | Description |
|----------|-------|-------------|
| Main Java Files | 12 | Core application logic (services, controllers, configs) |
| Test Java Files | 2+ | Unit tests for security sanitization and policy validation |
| Documentation MD Files | 6 | ARCHITECTURE.md, PROJECT_SUMMARY.md, TECHNOLOGY.md, README.md, TEST_CASES.md, CHUNK_LOG.md |

### Code Statistics

- **Total Lines of Code (LOC)**: ~800+ Java lines across main source files  
- **Test Coverage**: Security sanitization and policy validation tested  
- **Security Features Documented**: CSP headers, header filtering, ownership data removal  

---

## Quick Reference Links

| File | Purpose |
|------|---------|
| [ARCHITECTURE.md](./ARCHITECTURE.md) | System design, modules, dependencies, data flow diagrams |
| [PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md) | High-level overview, goals, scope documentation |
| [TECHNOLOGY.md](./TECHNOLOGY.md) | Technology stack, versions, build tools used |
| [TEST_CASES.md](./TEST_CASES.md) | Comprehensive test cases for all functionality and edge cases |

---

## Notes for Developers

1. **Adding New Test Cases**: Append to TEST_CASES.md following the established format  
2. **Architecture Changes**: Update ARCHITECTURE.md with new modules or data flows  
3. **Technology Updates**: Document any library version changes in TECHNOLOGY.md  



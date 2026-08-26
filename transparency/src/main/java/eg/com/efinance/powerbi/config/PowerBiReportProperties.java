package eg.com.efinance.powerbi.config;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import java.time.Duration;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

/**
 * Connection settings for the upstream Power BI Report Server.
 *
 * <p>Bound from {@code powerbi.report-server.*}. Credentials are never logged; see
 * {@link StartupConfigurationLogger} for the redacted startup summary.
 */
@Validated
@ConfigurationProperties(prefix = "powerbi.report-server")
public class PowerBiReportProperties {

    public enum AuthType {
        BASIC,
        NTLM,
        NONE
    }

    /** Base URL of the report server, e.g. {@code https://reports.example.com}. */
    @NotBlank
    private String baseUrl;

    /** Host the proxy is allowed to talk to. Any other host is rejected before a socket is opened. */
    @NotBlank
    private String allowedHost;

    private String username;

    private String password;

    /** NTLM domain. May also be supplied inside {@code username} as {@code DOMAIN\\user}. */
    private String domain;

    /** NTLM workstation. Optional. */
    private String workstation;

    @NotNull
    private AuthType authType = AuthType.BASIC;

    /** Absolute report URL. When blank, {@code baseUrl + transparencyReportPath} is used. */
    private String reportUrl;

    @NotBlank
    private String transparencyReportPath;

    @NotEmpty
    private List<String> allowedResourcePathPrefixes = List.of();

    @NotNull
    private Duration connectTimeout = Duration.ofSeconds(5);

    @NotNull
    private Duration responseTimeout = Duration.ofSeconds(60);

    /** Maximum proxied response body size held in memory before the request is failed. */
    @NotNull
    private org.springframework.util.unit.DataSize maxResponseSize =
            org.springframework.util.unit.DataSize.ofMegabytes(32);

    private final Pool pool = new Pool();

    public static class Pool {
        private int maxTotal = 20;
        private int maxPerRoute = 10;
        private Duration idleEviction = Duration.ofMinutes(5);
        private Duration validateAfterInactivity = Duration.ofSeconds(5);

        public int getMaxTotal() {
            return maxTotal;
        }

        public void setMaxTotal(int maxTotal) {
            this.maxTotal = maxTotal;
        }

        public int getMaxPerRoute() {
            return maxPerRoute;
        }

        public void setMaxPerRoute(int maxPerRoute) {
            this.maxPerRoute = maxPerRoute;
        }

        public Duration getIdleEviction() {
            return idleEviction;
        }

        public void setIdleEviction(Duration idleEviction) {
            this.idleEviction = idleEviction;
        }

        public Duration getValidateAfterInactivity() {
            return validateAfterInactivity;
        }

        public void setValidateAfterInactivity(Duration validateAfterInactivity) {
            this.validateAfterInactivity = validateAfterInactivity;
        }
    }

    /**
     * Domain portion of the configured credentials: the explicit {@code domain} property when set,
     * otherwise the {@code DOMAIN\\user} prefix of the username.
     */
    public String resolvedDomain() {
        if (domain != null && !domain.isBlank()) {
            return domain.trim();
        }
        int separator = username == null ? -1 : username.indexOf('\\');
        return separator > 0 ? username.substring(0, separator).trim() : null;
    }

    /** Username with any {@code DOMAIN\\} prefix stripped. */
    public String resolvedUsername() {
        if (username == null) {
            return null;
        }
        int separator = username.indexOf('\\');
        return separator >= 0 ? username.substring(separator + 1).trim() : username.trim();
    }

    /** Absolute URL of the transparency report on the upstream server. */
    public String resolvedReportUrl() {
        if (reportUrl != null && !reportUrl.isBlank()) {
            return reportUrl.trim();
        }
        return trimTrailingSlash(baseUrl) + ensureLeadingSlash(transparencyReportPath);
    }

    private static String trimTrailingSlash(String value) {
        return value.endsWith("/") ? value.substring(0, value.length() - 1) : value;
    }

    private static String ensureLeadingSlash(String value) {
        return value.startsWith("/") ? value : "/" + value;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public void setBaseUrl(String baseUrl) {
        this.baseUrl = baseUrl;
    }

    public String getAllowedHost() {
        return allowedHost;
    }

    public void setAllowedHost(String allowedHost) {
        this.allowedHost = allowedHost;
    }

    public String getUsername() {
        return username;
    }

    public void setUsername(String username) {
        this.username = username;
    }

    public String getPassword() {
        return password;
    }

    public void setPassword(String password) {
        this.password = password;
    }

    public String getDomain() {
        return domain;
    }

    public void setDomain(String domain) {
        this.domain = domain;
    }

    public String getWorkstation() {
        return workstation;
    }

    public void setWorkstation(String workstation) {
        this.workstation = workstation;
    }

    public AuthType getAuthType() {
        return authType;
    }

    public void setAuthType(AuthType authType) {
        this.authType = authType;
    }

    public String getReportUrl() {
        return reportUrl;
    }

    public void setReportUrl(String reportUrl) {
        this.reportUrl = reportUrl;
    }

    public String getTransparencyReportPath() {
        return transparencyReportPath;
    }

    public void setTransparencyReportPath(String transparencyReportPath) {
        this.transparencyReportPath = transparencyReportPath;
    }

    public List<String> getAllowedResourcePathPrefixes() {
        return allowedResourcePathPrefixes;
    }

    public void setAllowedResourcePathPrefixes(List<String> allowedResourcePathPrefixes) {
        this.allowedResourcePathPrefixes = allowedResourcePathPrefixes;
    }

    public Duration getConnectTimeout() {
        return connectTimeout;
    }

    public void setConnectTimeout(Duration connectTimeout) {
        this.connectTimeout = connectTimeout;
    }

    public Duration getResponseTimeout() {
        return responseTimeout;
    }

    public void setResponseTimeout(Duration responseTimeout) {
        this.responseTimeout = responseTimeout;
    }

    public org.springframework.util.unit.DataSize getMaxResponseSize() {
        return maxResponseSize;
    }

    public void setMaxResponseSize(org.springframework.util.unit.DataSize maxResponseSize) {
        this.maxResponseSize = maxResponseSize;
    }

    public Pool getPool() {
        return pool;
    }
}

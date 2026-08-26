package eg.com.efinance.powerbi.service;

import eg.com.efinance.powerbi.config.PowerBiReportProperties;
import eg.com.efinance.powerbi.web.ForbiddenProxyRequestException;
import eg.com.efinance.powerbi.web.ReportServerUnavailableException;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.util.Locale;
import java.util.Map;
import java.util.stream.Collectors;
import org.apache.hc.client5.http.classic.methods.HttpGet;
import org.apache.hc.client5.http.classic.methods.HttpHead;
import org.apache.hc.client5.http.classic.methods.HttpUriRequestBase;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.core5.http.ConnectionClosedException;
import org.apache.hc.core5.http.Header;
import org.apache.hc.core5.http.HttpEntity;
import org.apache.hc.core5.http.io.entity.EntityUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;

/**
 * Fetches report resources from the upstream server on the browser's behalf.
 *
 * <p>Every request passes the same four gates in order: path policy, host allow-list, header
 * filtering, then content post-processing (JSON minimisation and HTML link rewriting). The browser
 * never learns the upstream host and never holds upstream credentials.
 */
@Service
public class PowerBiProxyService {

    private static final Logger log = LoggerFactory.getLogger(PowerBiProxyService.class);
    private static final Logger audit = LoggerFactory.getLogger("AUDIT_POWERBI_REPORT_ACCESS");

    /** Same-origin prefix under which every upstream resource is republished. */
    public static final String PROXY_PREFIX = "/reports/powerbi/proxy";

    private static final String SESSION_COOKIE_PREFIX = "PBIRS_";

    private final CloseableHttpClient httpClient;
    private final PowerBiReportProperties properties;
    private final ProxyPathSecurityPolicy pathPolicy;
    private final SensitiveJsonSanitizer sanitizer;
    private final HeaderPolicy headerPolicy;
    private final UpstreamLinkRewriter linkRewriter;
    private final MeterRegistry meterRegistry;

    public PowerBiProxyService(
            CloseableHttpClient httpClient,
            PowerBiReportProperties properties,
            ProxyPathSecurityPolicy pathPolicy,
            SensitiveJsonSanitizer sanitizer,
            HeaderPolicy headerPolicy,
            UpstreamLinkRewriter linkRewriter,
            MeterRegistry meterRegistry) {
        this.httpClient = httpClient;
        this.properties = properties;
        this.pathPolicy = pathPolicy;
        this.sanitizer = sanitizer;
        this.headerPolicy = headerPolicy;
        this.linkRewriter = linkRewriter;
        this.meterRegistry = meterRegistry;
    }

    /** Same-origin URL the viewer shell points its frame at. */
    public String transparencyFrameUrl() {
        URI report = URI.create(properties.resolvedReportUrl());
        String path = report.getRawPath() == null || report.getRawPath().isBlank() ? "/" : report.getRawPath();
        String query = report.getRawQuery();
        return PROXY_PREFIX + path + (query == null || query.isBlank() ? "" : "?" + query);
    }

    /**
     * The report's path on the source system — shown in the shell's provenance strip.
     *
     * <p>Path only: the upstream host stays server-side, which is the same reason the response
     * header filter drops the Report Server's version banners.
     */
    public String reportSourcePath() {
        URI report = URI.create(properties.resolvedReportUrl());
        String path = report.getPath();
        return path == null || path.isBlank() ? properties.getTransparencyReportPath() : path;
    }

    public ProxyResponse proxy(String upstreamPath, HttpServletRequest request) {
        pathPolicy.validate(upstreamPath);

        URI target = buildTargetUri(upstreamPath, request.getQueryString());
        assertAllowedHost(target);

        Timer.Sample sample = Timer.start(meterRegistry);
        String outcome = "error";
        int status = 0;
        try {
            ProxyResponse response = execute(target, request);
            status = response.status();
            outcome = status < 400 ? "success" : "upstream_" + status;
            return response;
        } finally {
            sample.stop(Timer.builder("powerbi.proxy.request")
                    .description("Upstream Power BI Report Server calls made on behalf of a viewer")
                    .tag("outcome", outcome)
                    .tag("status", Integer.toString(status))
                    .register(meterRegistry));
        }
    }

    private ProxyResponse execute(URI target, HttpServletRequest request) {
        HttpUriRequestBase upstreamRequest =
                "HEAD".equalsIgnoreCase(request.getMethod()) ? new HttpHead(target) : new HttpGet(target);

        headerPolicy.forwardableHeaders(request).forEach(upstreamRequest::setHeader);
        sessionCookieHeader(request).ifPresent(value -> upstreamRequest.setHeader("Cookie", value));
        upstreamRequest.setHeader("Accept-Encoding", "identity");

        int maxBytes = (int) Math.min(Integer.MAX_VALUE, properties.getMaxResponseSize().toBytes());
        boolean secure = request.isSecure();

        try {
            return httpClient.execute(upstreamRequest, response -> {
                HttpEntity entity = response.getEntity();
                byte[] body = entity == null ? new byte[0] : readBody(entity, maxBytes);
                String contentType = entity == null ? null : entity.getContentType();

                HttpHeaders headers = new HttpHeaders();
                for (Header header : response.getHeaders()) {
                    if (headerPolicy.isForwardableResponseHeader(header.getName())) {
                        headers.add(header.getName(), header.getValue());
                    }
                }
                for (Header header : response.getHeaders("Set-Cookie")) {
                    if (headerPolicy.isSessionCookie(header.getValue())) {
                        headers.add(HttpHeaders.SET_COOKIE, headerPolicy.harden(header.getValue(), secure));
                    }
                }

                body = postProcess(body, contentType);
                if (contentType != null) {
                    headers.set(HttpHeaders.CONTENT_TYPE, contentType);
                }
                headers.setContentLength(body.length);
                return ProxyResponse.of(response.getCode(), headers, body);
            });
        } catch (ConnectionClosedException ex) {
            throw new ReportServerUnavailableException("Upstream closed the connection", ex);
        } catch (IOException ex) {
            log.warn("Upstream call to {} failed: {}", target.getPath(), ex.toString());
            throw new ReportServerUnavailableException("Report server is not reachable", ex);
        }
    }

    /** Bounded read: a runaway upstream response must not become an out-of-memory error here. */
    private byte[] readBody(HttpEntity entity, int maxBytes) throws IOException {
        return EntityUtils.toByteArray(entity, maxBytes);
    }

    /** JSON is minimised; HTML has its upstream links pulled back onto this origin. */
    private byte[] postProcess(byte[] body, String contentType) {
        if (body.length == 0 || contentType == null) {
            return body;
        }
        String type = contentType.toLowerCase(Locale.ROOT);
        if (type.contains(MediaType.APPLICATION_JSON_VALUE) || type.contains("+json")) {
            return sanitizer.sanitize(body);
        }
        if (type.contains(MediaType.TEXT_HTML_VALUE)) {
            return linkRewriter.rewrite(body, PROXY_PREFIX);
        }
        return body;
    }

    private java.util.Optional<String> sessionCookieHeader(HttpServletRequest request) {
        Cookie[] cookies = request.getCookies();
        if (cookies == null) {
            return java.util.Optional.empty();
        }
        String header = java.util.Arrays.stream(cookies)
                .filter(cookie -> cookie.getName() != null
                        && cookie.getName().toUpperCase(Locale.ROOT).startsWith(SESSION_COOKIE_PREFIX))
                .map(cookie -> cookie.getName() + "=" + cookie.getValue())
                .collect(Collectors.joining("; "));
        return header.isBlank() ? java.util.Optional.empty() : java.util.Optional.of(header);
    }

    private URI buildTargetUri(String upstreamPath, String queryString) {
        String base = properties.getBaseUrl().endsWith("/")
                ? properties.getBaseUrl().substring(0, properties.getBaseUrl().length() - 1)
                : properties.getBaseUrl();
        String path = upstreamPath.startsWith("/") ? upstreamPath : "/" + upstreamPath;
        String candidate = base + path + (queryString == null || queryString.isBlank() ? "" : "?" + queryString);
        try {
            return new URI(candidate).normalize();
        } catch (URISyntaxException ex) {
            throw new ForbiddenProxyRequestException(upstreamPath, "unparseable upstream URL");
        }
    }

    /**
     * The host allow-list is the last line of defence against a path crafted to escape the base URL
     * (protocol-relative prefixes, embedded credentials, normalisation tricks).
     */
    private void assertAllowedHost(URI target) {
        String host = target.getHost();
        if (host == null || !host.equalsIgnoreCase(properties.getAllowedHost())) {
            throw new ForbiddenProxyRequestException(target.toString(), "host is not allow-listed");
        }
        if (target.getUserInfo() != null) {
            throw new ForbiddenProxyRequestException(target.toString(), "credentials embedded in URL");
        }
    }

    /** One structured line per report open, for the access trail the compliance tests expect. */
    public void auditReportAccess(HttpServletRequest request, String principal) {
        audit.info(
                "event=transparency_report_open principal=\"{}\" client={} requestId={} userAgent=\"{}\"",
                principal,
                request.getRemoteAddr(),
                request.getAttribute("requestId"),
                sanitizeForLog(request.getHeader(HttpHeaders.USER_AGENT)));
    }

    private static String sanitizeForLog(String value) {
        if (value == null) {
            return "";
        }
        return value.replaceAll("[\\r\\n\"]", " ").trim();
    }

    /** Exposed for the shell's connectivity probe. */
    public Map<String, Object> upstreamDescriptor() {
        return Map.of(
                "reportName", properties.getTransparencyReportPath(),
                "authType", properties.getAuthType().name(),
                "responseTimeoutSeconds", properties.getResponseTimeout().toSeconds());
    }
}

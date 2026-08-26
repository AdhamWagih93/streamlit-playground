package eg.com.efinance.powerbi.service;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Collections;
import java.util.Enumeration;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;

/**
 * The header contract between browser, proxy and Report Server.
 *
 * <p>Two independent filters. Inbound: hop-by-hop headers and anything that would leak the
 * browser's own credentials or origin to the upstream server. Outbound: version banners and
 * framing/CSP headers from the Report Server, which would otherwise override the policy this
 * application sets and disclose the upstream stack.
 */
@Component
public class HeaderPolicy {

    private static final Set<String> DROPPED_REQUEST_HEADERS = Set.of(
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "host",
            "authorization",
            "cookie",
            "origin",
            "referer",
            "accept-encoding",
            "cache-control",
            "pragma",
            "priority",
            "content-length",
            "expect",
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto");

    private static final Set<String> DROPPED_RESPONSE_HEADERS = Set.of(
            "connection",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
            "content-length",
            "content-encoding",
            "x-frame-options",
            "content-security-policy",
            "content-security-policy-report-only",
            "strict-transport-security",
            "x-aspnet-version",
            "x-aspnetmvc-version",
            "x-powered-by",
            "server",
            "x-reportserver-version",
            "x-sql-reporting-services-version",
            "www-authenticate",
            "set-cookie");

    /** Cookies the viewer genuinely needs to keep its upstream session alive. */
    private static final String SESSION_COOKIE_PREFIX = "pbirs_";

    public boolean isForwardableRequestHeader(String name) {
        String lower = name.toLowerCase(Locale.ROOT);
        return !DROPPED_REQUEST_HEADERS.contains(lower) && !lower.startsWith("sec-fetch-") && !lower.startsWith("sec-ch-");
    }

    public boolean isForwardableResponseHeader(String name) {
        return !DROPPED_RESPONSE_HEADERS.contains(name.toLowerCase(Locale.ROOT));
    }

    /** True for {@code Set-Cookie} values that carry the upstream viewer session. */
    public boolean isSessionCookie(String setCookieValue) {
        if (setCookieValue == null) {
            return false;
        }
        return setCookieValue.toLowerCase(Locale.ROOT).startsWith(SESSION_COOKIE_PREFIX);
    }

    /** Rewrites an upstream cookie so it is scoped to this origin and not readable from script. */
    public String harden(String setCookieValue, boolean secureRequest) {
        StringBuilder cookie = new StringBuilder(stripAttribute(stripAttribute(setCookieValue, "domain"), "secure"));
        if (!setCookieValue.toLowerCase(Locale.ROOT).contains("httponly")) {
            cookie.append("; HttpOnly");
        }
        if (!setCookieValue.toLowerCase(Locale.ROOT).contains("samesite")) {
            cookie.append("; SameSite=Lax");
        }
        if (secureRequest) {
            cookie.append("; Secure");
        }
        return cookie.toString();
    }

    private static String stripAttribute(String cookie, String attribute) {
        String[] parts = cookie.split(";");
        StringBuilder result = new StringBuilder();
        for (String part : parts) {
            String trimmed = part.trim();
            if (trimmed.toLowerCase(Locale.ROOT).startsWith(attribute)) {
                continue;
            }
            if (result.length() > 0) {
                result.append("; ");
            }
            result.append(trimmed);
        }
        return result.toString();
    }

    /** Request headers that survive the inbound filter, preserving original casing. */
    public Map<String, String> forwardableHeaders(HttpServletRequest request) {
        Map<String, String> headers = new LinkedHashMap<>();
        Enumeration<String> names = request.getHeaderNames();
        if (names == null) {
            return headers;
        }
        for (String name : Collections.list(names)) {
            if (isForwardableRequestHeader(name)) {
                headers.put(name, request.getHeader(name));
            }
        }
        return headers;
    }
}

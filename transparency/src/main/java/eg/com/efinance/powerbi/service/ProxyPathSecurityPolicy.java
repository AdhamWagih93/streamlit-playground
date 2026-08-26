package eg.com.efinance.powerbi.service;

import eg.com.efinance.powerbi.config.PowerBiReportProperties;
import eg.com.efinance.powerbi.web.ForbiddenProxyRequestException;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

/**
 * Deny-by-default gate in front of the upstream server.
 *
 * <p>A Report Server exposes far more than one report: catalog listings, data sources, schedules,
 * subscriptions and OData metadata all sit on the same host. The transparency dashboard needs
 * exactly one report and its assets, so this policy allows that shape and refuses everything else.
 *
 * <p>Order matters. Paths are decoded repeatedly before matching, because {@code %2524metadata}
 * decodes to {@code %24metadata} and then to {@code $metadata} — a single decode pass would let it
 * through and the servlet container would resolve it upstream.
 */
@Component
public class ProxyPathSecurityPolicy {

    private static final int MAX_DECODE_PASSES = 3;

    /** Administrative collections: never reachable, keyed or not. */
    private static final Set<String> BLOCKED_RESOURCES = Set.of(
            "alertsubscriptions",
            "datasources",
            "schedules",
            "subscriptions",
            "system",
            "telemetry",
            "usersettings",
            "me",
            "session",
            "securitypolicies");

    /** Collections that enumerate the catalog unless a specific item key is supplied. */
    private static final Set<String> KEY_REQUIRED_RESOURCES =
            Set.of("catalogitems", "powerbireports", "reports", "folders", "mobilereports", "linkedreports");

    /**
     * Folder-browsing entry points of the built-in web portal. Matched as path prefixes, not as
     * substrings — an asset legitimately named {@code home.js} must still be servable.
     */
    private static final List<String> BLOCKED_PORTAL_PREFIXES = List.of(
            "/browse",
            "/home",
            "/manage",
            "/reports/browse",
            "/reports/home",
            "/reports/manage",
            "/reports/settings",
            "/reportserver/browse",
            "/powerbi/browse",
            "/powerbi/home");

    /** Substrings that are unsafe anywhere in a path: OData metadata and batch endpoints. */
    private static final List<String> BLOCKED_SUBSTRINGS = List.of("$metadata", "$batch");

    private static final Pattern API_ROOT = Pattern.compile("^/api/v\\d+(\\.\\d+)?/?$", Pattern.CASE_INSENSITIVE);
    private static final Pattern API_RESOURCE =
            Pattern.compile("^/api/v\\d+(?:\\.\\d+)?/([^/(]+)(\\(([^)]*)\\))?(/.*)?$", Pattern.CASE_INSENSITIVE);

    private final PowerBiReportProperties properties;

    public ProxyPathSecurityPolicy(PowerBiReportProperties properties) {
        this.properties = properties;
    }

    /** @throws ForbiddenProxyRequestException if the path may not be proxied. */
    public void validate(String rawPath) {
        String reason = rejectionReason(rawPath);
        if (reason != null) {
            throw new ForbiddenProxyRequestException(rawPath, reason);
        }
    }

    public boolean isAllowed(String rawPath) {
        return rejectionReason(rawPath) == null;
    }

    /** @return null when the path is allowed, otherwise a short reason suitable for logging. */
    private String rejectionReason(String rawPath) {
        if (rawPath == null || rawPath.isBlank()) {
            return "empty path";
        }

        String decoded;
        try {
            decoded = decodeRepeatedly(rawPath);
        } catch (IllegalArgumentException ex) {
            return "malformed percent-encoding";
        }

        String path = decoded.startsWith("/") ? decoded : "/" + decoded;
        String lower = path.toLowerCase(Locale.ROOT);

        if (lower.contains("..") || lower.contains("\\") || containsControlCharacter(path)) {
            return "path traversal or control characters";
        }
        if (!matchesAllowedPrefix(lower)) {
            return "outside allowed resource prefixes";
        }
        for (String prefix : BLOCKED_PORTAL_PREFIXES) {
            if (lower.equals(prefix) || lower.startsWith(prefix + "/")) {
                return "portal browsing path: " + prefix;
            }
        }
        for (String fragment : BLOCKED_SUBSTRINGS) {
            if (lower.contains(fragment)) {
                return "blocked path fragment: " + fragment;
            }
        }
        if (API_ROOT.matcher(lower).matches()) {
            return "API root enumeration";
        }

        var matcher = API_RESOURCE.matcher(path);
        if (matcher.matches()) {
            String resource = matcher.group(1).toLowerCase(Locale.ROOT);
            String key = matcher.group(3);
            boolean keyed = key != null && !key.isBlank();

            if (BLOCKED_RESOURCES.contains(resource)) {
                return "administrative resource: " + resource;
            }
            if (KEY_REQUIRED_RESOURCES.contains(resource) && !keyed) {
                return "unkeyed collection: " + resource;
            }
        }
        return null;
    }

    private boolean matchesAllowedPrefix(String lowerPath) {
        for (String prefix : properties.getAllowedResourcePathPrefixes()) {
            if (prefix == null || prefix.isBlank()) {
                continue;
            }
            String candidate = prefix.toLowerCase(Locale.ROOT);
            if (!candidate.startsWith("/")) {
                candidate = "/" + candidate;
            }
            if (lowerPath.startsWith(candidate)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Decodes until the value stops changing, capped at {@value #MAX_DECODE_PASSES} passes so a
     * deeply nested encoding cannot spin the CPU.
     */
    static String decodeRepeatedly(String value) {
        String current = value;
        for (int pass = 0; pass < MAX_DECODE_PASSES; pass++) {
            String next = URLDecoder.decode(current, StandardCharsets.UTF_8);
            if (next.equals(current)) {
                return current;
            }
            current = next;
        }
        return current;
    }

    private static boolean containsControlCharacter(String value) {
        for (int i = 0; i < value.length(); i++) {
            if (Character.isISOControl(value.charAt(i))) {
                return true;
            }
        }
        return false;
    }
}

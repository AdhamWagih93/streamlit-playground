package eg.com.efinance.powerbi.web;

import java.util.List;
import java.util.Locale;

/**
 * Single source of truth for "is this URL served by the proxy?".
 *
 * <p>Shared by the security filter chain (CSRF exemptions), the CSP filter and the rate limiter, so
 * the three can never drift apart — a class of bug that shows up as a report that renders in one
 * environment and is blocked in the next.
 */
public final class ProxyPathMatcher {

    /** Ant-style patterns registered with Spring Security for CSRF and CSP purposes. */
    public static final List<String> PROXY_PATTERNS = List.of(
            "/reports/powerbi/proxy/**",
            "/powerbi/**",
            "/PowerBI/**",
            "/ReportServer/**",
            "/Reserved.ReportViewerWebControl.axd",
            "/Reports/**",
            "/api/**",
            "/explore/**",
            "/modelsAndExploration/**",
            "/querydata/**",
            "/metadata/**",
            "/resources/**",
            "/public/**",
            "/13.0.*/**");

    private static final List<String> PREFIXES = List.of(
            "/reports/powerbi/proxy/",
            "/powerbi/",
            "/reportserver/",
            "/reserved.reportviewerwebcontrol.axd",
            "/reports/",
            "/api/",
            "/explore/",
            "/modelsandexploration/",
            "/querydata/",
            "/metadata/",
            "/resources/",
            "/public/",
            "/13.0.");

    private ProxyPathMatcher() {}

    public static ProxyPathMatcher create() {
        return new ProxyPathMatcher();
    }

    public boolean isProxied(String requestUri) {
        if (requestUri == null || requestUri.isBlank()) {
            return false;
        }
        String path = requestUri.toLowerCase(Locale.ROOT);
        // The shell and its status probe live under /reports/powerbi/ but are ours, not the
        // upstream server's, and must keep the strict shell policy.
        if (path.equals("/reports/powerbi/transparency")
                || path.startsWith("/reports/powerbi/transparency/")
                || path.equals("/reports/powerbi/status")) {
            return false;
        }
        return PREFIXES.stream().anyMatch(path::startsWith);
    }
}

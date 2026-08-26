package eg.com.efinance.powerbi.service;

import eg.com.efinance.powerbi.config.PowerBiReportProperties;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Locale;
import org.jsoup.Jsoup;
import org.jsoup.nodes.Document;
import org.jsoup.nodes.Element;
import org.springframework.stereotype.Component;

/**
 * Rewrites absolute upstream URLs in proxied HTML so they resolve back through this proxy.
 *
 * <p>Without this, a report page loads through the proxy and then reaches straight past it for its
 * own assets — which the browser blocks under our CSP, and which would bypass the path policy if it
 * did not. Rewriting keeps every subsequent request on this origin.
 */
@Component
public class UpstreamLinkRewriter {

    private static final List<String> URL_ATTRIBUTES = List.of("href", "src", "action", "data-src", "poster");

    private final PowerBiReportProperties properties;

    public UpstreamLinkRewriter(PowerBiReportProperties properties) {
        this.properties = properties;
    }

    public byte[] rewrite(byte[] html, String proxyPrefix) {
        if (html == null || html.length == 0) {
            return html;
        }
        String source = new String(html, StandardCharsets.UTF_8);
        Document document = Jsoup.parse(source, properties.getBaseUrl());
        document.outputSettings().prettyPrint(false);

        // A <base> element would re-anchor every relative URL at the upstream host.
        document.select("base[href]").forEach(Element::remove);

        for (String attribute : URL_ATTRIBUTES) {
            for (Element element : document.select("[" + attribute + "]")) {
                String value = element.attr(attribute);
                String rewritten = rewriteUrl(value, proxyPrefix);
                if (!rewritten.equals(value)) {
                    element.attr(attribute, rewritten);
                }
            }
        }
        return document.outerHtml().getBytes(StandardCharsets.UTF_8);
    }

    /** Absolute upstream URLs become proxy-relative; everything else is left untouched. */
    String rewriteUrl(String value, String proxyPrefix) {
        if (value == null || value.isBlank()) {
            return value;
        }
        String base = properties.getBaseUrl().endsWith("/")
                ? properties.getBaseUrl().substring(0, properties.getBaseUrl().length() - 1)
                : properties.getBaseUrl();

        if (value.toLowerCase(Locale.ROOT).startsWith(base.toLowerCase(Locale.ROOT))) {
            String remainder = value.substring(base.length());
            return proxyPrefix + (remainder.startsWith("/") ? remainder : "/" + remainder);
        }
        if (value.startsWith("//")) {
            int slash = value.indexOf('/', 2);
            String host = slash < 0 ? value.substring(2) : value.substring(2, slash);
            if (host.equalsIgnoreCase(properties.getAllowedHost())) {
                return proxyPrefix + (slash < 0 ? "/" : value.substring(slash));
            }
        }
        return value;
    }
}

package eg.com.efinance.powerbi.service;

import static org.assertj.core.api.Assertions.assertThat;

import eg.com.efinance.powerbi.config.PowerBiReportProperties;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class UpstreamLinkRewriterTest {

    private static final String PREFIX = "/reports/powerbi/proxy";

    private UpstreamLinkRewriter rewriter;

    @BeforeEach
    void setUp() {
        var properties = new PowerBiReportProperties();
        properties.setBaseUrl("https://reports.example.com");
        properties.setAllowedHost("reports.example.com");
        properties.setTransparencyReportPath("/Reports/Transparency");
        properties.setAllowedResourcePathPrefixes(List.of("/Reports/"));
        rewriter = new UpstreamLinkRewriter(properties);
    }

    @Test
    @DisplayName("Absolute upstream URLs are pulled back onto this origin")
    void rewritesAbsoluteUpstreamUrls() {
        assertThat(rewriter.rewriteUrl("https://reports.example.com/Reports/app.js", PREFIX))
                .isEqualTo(PREFIX + "/Reports/app.js");
    }

    @Test
    @DisplayName("Protocol-relative URLs to the allowed host are rewritten too")
    void rewritesProtocolRelativeUrls() {
        assertThat(rewriter.rewriteUrl("//reports.example.com/Reports/app.js", PREFIX))
                .isEqualTo(PREFIX + "/Reports/app.js");
    }

    @Test
    @DisplayName("Third-party and relative URLs are left alone")
    void leavesForeignUrlsAlone() {
        assertThat(rewriter.rewriteUrl("https://cdn.example.net/x.js", PREFIX)).isEqualTo("https://cdn.example.net/x.js");
        assertThat(rewriter.rewriteUrl("/Reports/app.js", PREFIX)).isEqualTo("/Reports/app.js");
        assertThat(rewriter.rewriteUrl("#anchor", PREFIX)).isEqualTo("#anchor");
    }

    @Test
    @DisplayName("A base element is removed so relative URLs stay on this origin")
    void stripsBaseElement() {
        String html = "<html><head><base href=\"https://reports.example.com/\"></head>"
                + "<body><script src=\"https://reports.example.com/Reports/app.js\"></script></body></html>";

        String result = new String(rewriter.rewrite(html.getBytes(StandardCharsets.UTF_8), PREFIX), StandardCharsets.UTF_8);

        assertThat(result).doesNotContain("<base");
        assertThat(result).contains(PREFIX + "/Reports/app.js");
    }
}

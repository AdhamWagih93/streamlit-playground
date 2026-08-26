package eg.com.efinance.powerbi.web;

import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.matchesRegex;
import static org.hamcrest.Matchers.not;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.redirectedUrl;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;

/** IT-002, FC-001, SEC-002, SEC-003: the viewer shell and its security headers, end to end. */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
    "app.security.enabled=false",
    "app.ui.report-name=Quarterly Transparency Report",
    "powerbi.report-server.base-url=https://reports.example.com",
    "powerbi.report-server.allowed-host=reports.example.com",
    "powerbi.report-server.auth-type=NONE",
    "powerbi.report-server.transparency-report-path=/Reports/powerbi/Transparency"
})
class TransparencyShellIntegrationTest {

    @Autowired
    private MockMvc mockMvc;

    /** No upstream in a unit test; the shell renders without one and reports the state honestly. */
    @MockBean
    private CloseableHttpClient httpClient;

    @Test
    @DisplayName("The root path leads to the dashboard")
    void rootRedirectsToDashboard() throws Exception {
        mockMvc.perform(get("/")).andExpect(status().is3xxRedirection())
                .andExpect(redirectedUrl("/reports/powerbi/transparency"));
    }

    @Test
    @DisplayName("The shell renders with the report framed on this origin")
    void rendersShell() throws Exception {
        mockMvc.perform(get("/reports/powerbi/transparency").accept(MediaType.TEXT_HTML))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_HTML))
                .andExpect(content().string(containsString("Quarterly Transparency Report")))
                .andExpect(content().string(containsString("src=\"/reports/powerbi/proxy/Reports/powerbi/Transparency\"")))
                // Provenance, not decoration: the strip states what was withheld.
                .andExpect(content().string(containsString("Ownership and authorship fields")))
                // The upstream host must not appear anywhere in the page.
                .andExpect(content().string(not(containsString("reports.example.com"))));
    }

    @Test
    @DisplayName("The shell is never cached and carries a correlation id")
    void setsNoStoreAndCorrelationId() throws Exception {
        mockMvc.perform(get("/reports/powerbi/transparency").accept(MediaType.TEXT_HTML))
                .andExpect(header().string(HttpHeaders.CACHE_CONTROL, containsString("no-store")))
                .andExpect(header().exists(CorrelationIdFilter.HEADER));
    }

    @Test
    @DisplayName("Shell pages get a nonce-based policy with no unsafe-inline")
    void shellUsesStrictCsp() throws Exception {
        mockMvc.perform(get("/reports/powerbi/transparency").accept(MediaType.TEXT_HTML))
                .andExpect(header().string("Content-Security-Policy", matchesRegex(".*script-src 'self' 'nonce-[A-Za-z0-9_-]+'.*")))
                .andExpect(header().string("Content-Security-Policy", not(containsString("unsafe-inline"))))
                .andExpect(header().string("X-Frame-Options", "SAMEORIGIN"));
    }

    @Test
    @DisplayName("Proxy paths keep the looser policy the viewer bundle needs")
    void proxyPathsUseViewerCsp() throws Exception {
        mockMvc.perform(get("/api/v2.0/DataSources").accept(MediaType.APPLICATION_JSON))
                .andExpect(header().string("Content-Security-Policy", containsString("unsafe-eval")));
    }

    @Test
    @DisplayName("A blocked path answers JSON to an API client and a page to a browser")
    void blockedPathNegotiatesFormat() throws Exception {
        mockMvc.perform(get("/api/v2.0/DataSources").accept(MediaType.APPLICATION_JSON))
                .andExpect(status().isForbidden())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_PROBLEM_JSON))
                .andExpect(jsonPath("$.title").value("Resource not available"))
                .andExpect(jsonPath("$.requestId").exists());

        mockMvc.perform(get("/api/v2.0/DataSources").accept(MediaType.TEXT_HTML))
                .andExpect(status().isForbidden())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_HTML))
                .andExpect(content().string(containsString("Resource not available")));
    }

    @Test
    @DisplayName("The health endpoint stays public")
    void healthIsPublic() throws Exception {
        mockMvc.perform(get("/actuator/health")).andExpect(status().isOk());
    }

    @Test
    @DisplayName("Browser noise is absorbed, not proxied")
    void absorbsBrowserNoise() throws Exception {
        mockMvc.perform(get("/favicon.ico")).andExpect(status().isNoContent());
        mockMvc.perform(get("/.well-known/appspecific/com.chrome.devtools.json")).andExpect(status().isNoContent());
    }
}

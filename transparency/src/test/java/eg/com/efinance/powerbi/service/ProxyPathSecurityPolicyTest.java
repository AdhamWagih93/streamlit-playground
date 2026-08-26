package eg.com.efinance.powerbi.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import eg.com.efinance.powerbi.config.PowerBiReportProperties;
import eg.com.efinance.powerbi.web.ForbiddenProxyRequestException;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

class ProxyPathSecurityPolicyTest {

    private ProxyPathSecurityPolicy policy;

    @BeforeEach
    void setUp() {
        var properties = new PowerBiReportProperties();
        properties.setBaseUrl("https://reports.example.com");
        properties.setAllowedHost("reports.example.com");
        properties.setTransparencyReportPath("/Reports/powerbi/Transparency");
        properties.setAllowedResourcePathPrefixes(List.of(
                "/Reports/",
                "/ReportServer/",
                "/Reserved.ReportViewerWebControl.axd",
                "/PowerBI/",
                "/powerbi/",
                "/api/",
                "/explore/",
                "/modelsAndExploration/",
                "/querydata/",
                "/metadata/",
                "/resources/",
                "/public/",
                "/13.0."));
        policy = new ProxyPathSecurityPolicy(properties);
    }

    // SC-001
    @ParameterizedTest
    @ValueSource(strings = {"/api/v1.0", "/api/v2.0/", "/api/v2.0/%24metadata", "/api/v2.0/%2524metadata", "/api/v2.0/$batch"})
    @DisplayName("OData metadata and API roots are blocked, single- or double-encoded")
    void blocksMetadataEndpoints(String path) {
        assertThat(policy.isAllowed(path)).isFalse();
    }

    // SC-002
    @ParameterizedTest
    @ValueSource(strings = {
        "/api/v2.0/DataSources",
        "/api/v2.0/Telemetry",
        "/api/v2.0/System/ReportServerRelativeUrl",
        "/api/v2.0/CatalogItems",
        "/api/v2.0/PowerBIReports",
        "/api/v2.0/Subscriptions",
        "/api/v2.0/Schedules",
        "/api/v2.0/AlertSubscriptions",
        "/api/v2.0/UserSettings"
    })
    @DisplayName("Administrative and enumerable collections are blocked")
    void blocksAdministrativeResources(String path) {
        assertThat(policy.isAllowed(path)).isFalse();
    }

    // SC-003
    @ParameterizedTest
    @ValueSource(strings = {"/Reports/browse", "/Reports/home", "/Reports/home/folder", "/powerbi/browse"})
    @DisplayName("Portal browsing paths are blocked")
    void blocksBrowsePaths(String path) {
        assertThat(policy.isAllowed(path)).isFalse();
    }

    // SC-004
    @ParameterizedTest
    @ValueSource(strings = {
        "/api/v2.0/PowerBIReports(123)",
        "/api/v2.0/CatalogItems(0d1b2c3d-4e5f-6789-abcd-ef0123456789)",
        "/Reports/powerbi/public/report",
        "/Reports/assets/js/runtime.js",
        "/Reports/assets/css/home.css",
        "/13.0.1234.5/ReportViewer.js"
    })
    @DisplayName("Keyed report resources and static assets are allowed")
    void allowsKeyedResources(String path) {
        assertThat(policy.isAllowed(path)).isTrue();
    }

    @Test
    @DisplayName("An asset whose name contains a blocked word is still served")
    void doesNotBlockOnSubstringAlone() {
        // Regression: blocking "/home" as a substring took out /Reports/assets/js/home.js with it.
        assertThat(policy.isAllowed("/Reports/assets/js/home.js")).isTrue();
        assertThat(policy.isAllowed("/Reports/assets/js/browse-widget.js")).isTrue();
    }

    // SC-005
    @ParameterizedTest
    @ValueSource(strings = {"/api/v2.0/%ZZ", "/Reports/..%2f..%2fetc/passwd", "/Reports/../../secret"})
    @DisplayName("Malformed encoding and traversal are blocked")
    void blocksDecodingAttacks(String path) {
        assertThat(policy.isAllowed(path)).isFalse();
    }

    // SC-006
    @ParameterizedTest
    @NullAndEmptySource
    @ValueSource(strings = {"   "})
    @DisplayName("Null and blank paths are blocked")
    void blocksBlankPaths(String path) {
        assertThat(policy.isAllowed(path)).isFalse();
    }

    @Test
    @DisplayName("Anything outside the configured prefixes is blocked by default")
    void deniesByDefault() {
        assertThat(policy.isAllowed("/etc/passwd")).isFalse();
        assertThat(policy.isAllowed("/actuator/env")).isFalse();
    }

    @Test
    @DisplayName("validate() reports the offending path")
    void validateThrowsWithPath() {
        assertThatThrownBy(() -> policy.validate("/api/v2.0/DataSources"))
                .isInstanceOf(ForbiddenProxyRequestException.class)
                .hasMessageContaining("administrative");
    }

    @Test
    @DisplayName("Repeated decoding stops after three passes")
    void decodingIsBounded() {
        assertThat(ProxyPathSecurityPolicy.decodeRepeatedly("%252524metadata")).isEqualTo("$metadata");
    }
}

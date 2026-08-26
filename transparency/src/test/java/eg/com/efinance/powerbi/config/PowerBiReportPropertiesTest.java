package eg.com.efinance.powerbi.config;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/** CFG-001..CFG-004: credential parsing and URL resolution. */
class PowerBiReportPropertiesTest {

    private PowerBiReportProperties properties() {
        var properties = new PowerBiReportProperties();
        properties.setBaseUrl("https://reports.example.com/");
        properties.setAllowedHost("reports.example.com");
        properties.setTransparencyReportPath("Reports/powerbi/Transparency");
        return properties;
    }

    @Test
    @DisplayName("A DOMAIN\\user credential splits into its parts")
    void splitsDomainQualifiedUsername() {
        var properties = properties();
        properties.setUsername("EFINANCE\\reportuser");

        assertThat(properties.resolvedDomain()).isEqualTo("EFINANCE");
        assertThat(properties.resolvedUsername()).isEqualTo("reportuser");
    }

    @Test
    @DisplayName("An explicit domain property wins over the username prefix")
    void explicitDomainWins() {
        var properties = properties();
        properties.setUsername("EFINANCE\\reportuser");
        properties.setDomain("OVERRIDE");

        assertThat(properties.resolvedDomain()).isEqualTo("OVERRIDE");
    }

    @Test
    @DisplayName("A plain username has no domain")
    void plainUsernameHasNoDomain() {
        var properties = properties();
        properties.setUsername("reportuser");

        assertThat(properties.resolvedDomain()).isNull();
        assertThat(properties.resolvedUsername()).isEqualTo("reportuser");
    }

    @Test
    @DisplayName("The report URL is composed from base and path, with slashes normalised")
    void composesReportUrl() {
        assertThat(properties().resolvedReportUrl())
                .isEqualTo("https://reports.example.com/Reports/powerbi/Transparency");
    }

    @Test
    @DisplayName("An explicit report URL is used verbatim")
    void explicitReportUrlWins() {
        var properties = properties();
        properties.setReportUrl("https://reports.example.com/Reports/Other?rs:Embed=true");

        assertThat(properties.resolvedReportUrl()).isEqualTo("https://reports.example.com/Reports/Other?rs:Embed=true");
    }
}

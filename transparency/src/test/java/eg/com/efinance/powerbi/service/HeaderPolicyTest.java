package eg.com.efinance.powerbi.service;

import static org.assertj.core.api.Assertions.assertThat;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class HeaderPolicyTest {

    private final HeaderPolicy policy = new HeaderPolicy();

    // RG-001
    @ParameterizedTest
    @ValueSource(strings = {"Connection", "keep-alive", "Host", "Authorization", "Cookie", "Origin", "Referer",
        "Accept-Encoding", "Sec-Fetch-Mode", "sec-ch-ua-platform", "Content-Length"})
    @DisplayName("Hop-by-hop and browser-credential headers are not forwarded upstream")
    void dropsHopByHopRequestHeaders(String header) {
        assertThat(policy.isForwardableRequestHeader(header)).isFalse();
    }

    @ParameterizedTest
    @ValueSource(strings = {"Accept", "Accept-Language", "User-Agent", "If-None-Match", "Range"})
    @DisplayName("Headers the report server needs are forwarded")
    void forwardsUsefulRequestHeaders(String header) {
        assertThat(policy.isForwardableRequestHeader(header)).isTrue();
    }

    // SS-004
    @ParameterizedTest
    @ValueSource(strings = {"Server", "X-AspNet-Version", "X-Powered-By", "X-ReportServer-Version",
        "X-SQL-Reporting-Services-Version", "X-Frame-Options", "Content-Security-Policy", "Set-Cookie",
        "WWW-Authenticate"})
    @DisplayName("Upstream version banners and framing headers never reach the browser")
    void dropsLeakyResponseHeaders(String header) {
        assertThat(policy.isForwardableResponseHeader(header)).isFalse();
    }

    @Test
    @DisplayName("Only PBIRS_ session cookies survive")
    void keepsOnlySessionCookies() {
        assertThat(policy.isSessionCookie("PBIRS_SessionId=abc; Path=/")).isTrue();
        assertThat(policy.isSessionCookie("ASP.NET_SessionId=abc; Path=/")).isFalse();
        assertThat(policy.isSessionCookie(null)).isFalse();
    }

    @Test
    @DisplayName("Forwarded cookies are re-scoped to this origin and hidden from script")
    void hardensCookies() {
        String hardened = policy.harden("PBIRS_SessionId=abc; Domain=reports.example.com; Path=/", true);

        assertThat(hardened).doesNotContain("Domain=");
        assertThat(hardened).contains("HttpOnly").contains("SameSite=Lax").contains("Secure");
        assertThat(hardened).startsWith("PBIRS_SessionId=abc");
    }

    @Test
    @DisplayName("Secure is not added on a plaintext request")
    void omitsSecureOnPlainHttp() {
        assertThat(policy.harden("PBIRS_SessionId=abc; Path=/", false)).doesNotContain("Secure");
    }
}

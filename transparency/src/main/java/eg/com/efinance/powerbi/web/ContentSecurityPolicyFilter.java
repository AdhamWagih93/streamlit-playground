package eg.com.efinance.powerbi.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.security.SecureRandom;
import java.util.Base64;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Emits a per-request Content-Security-Policy.
 *
 * <p>Two policies, because two very different kinds of response leave this application:
 *
 * <ul>
 *   <li><b>Shell pages</b> we author ourselves get a strict, nonce-based policy — no
 *       {@code unsafe-inline}, no {@code unsafe-eval}, {@code default-src 'none'}.
 *   <li><b>Proxied report resources</b> get the looser policy the Power BI viewer bundle requires.
 *       Widening that policy for the whole application, as a single static header would, is what
 *       the split avoids.
 * </ul>
 */
public class ContentSecurityPolicyFilter extends OncePerRequestFilter {

    public static final String NONCE_ATTRIBUTE = "cspNonce";

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();

    private static final String PROXY_POLICY = "default-src 'self'; "
            + "frame-src 'self'; frame-ancestors 'self'; "
            + "img-src 'self' data: blob:; "
            + "style-src 'self' 'unsafe-inline'; "
            + "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            + "connect-src 'self' blob:; "
            + "font-src 'self' data:; "
            + "worker-src 'self' blob:; "
            + "object-src 'none'; base-uri 'none'";

    private final ProxyPathMatcher proxyPaths;

    public ContentSecurityPolicyFilter(ProxyPathMatcher proxyPaths) {
        this.proxyPaths = proxyPaths;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String path = request.getRequestURI();
        if (proxyPaths.isProxied(path)) {
            response.setHeader("Content-Security-Policy", PROXY_POLICY);
        } else {
            byte[] entropy = new byte[16];
            RANDOM.nextBytes(entropy);
            String nonce = ENCODER.encodeToString(entropy);
            request.setAttribute(NONCE_ATTRIBUTE, nonce);
            response.setHeader("Content-Security-Policy", shellPolicy(nonce));
        }
        chain.doFilter(request, response);
    }

    private static String shellPolicy(String nonce) {
        return "default-src 'none'; "
                + "script-src 'self' 'nonce-" + nonce + "'; "
                + "style-src 'self' 'nonce-" + nonce + "'; "
                + "img-src 'self' data:; "
                + "font-src 'self'; "
                + "connect-src 'self'; "
                + "frame-src 'self'; frame-ancestors 'self'; "
                + "form-action 'self'; base-uri 'none'; object-src 'none'";
    }

    @Override
    protected boolean shouldNotFilterErrorDispatch() {
        return false;
    }
}

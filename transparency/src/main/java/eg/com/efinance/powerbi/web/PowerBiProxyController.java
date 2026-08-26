package eg.com.efinance.powerbi.web;

import eg.com.efinance.powerbi.service.PowerBiProxyService;
import eg.com.efinance.powerbi.service.ProxyResponse;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * The canonical proxy entry point: {@code /reports/powerbi/proxy/**} maps 1:1 onto upstream paths.
 */
@RestController
public class PowerBiProxyController {

    private final PowerBiProxyService proxyService;

    public PowerBiProxyController(PowerBiProxyService proxyService) {
        this.proxyService = proxyService;
    }

    @RequestMapping(value = PowerBiProxyService.PROXY_PREFIX + "/**", method = {RequestMethod.GET, RequestMethod.HEAD})
    public ResponseEntity<byte[]> proxy(HttpServletRequest request) {
        return respond(proxyService.proxy(upstreamPath(request), request));
    }

    @GetMapping(PowerBiProxyService.PROXY_PREFIX)
    public ResponseEntity<byte[]> proxyRoot(HttpServletRequest request) {
        return respond(proxyService.proxy("/", request));
    }

    static ResponseEntity<byte[]> respond(ProxyResponse response) {
        return ResponseEntity.status(response.status()).headers(response.headers()).body(response.body());
    }

    /**
     * The raw, still-encoded path beyond the proxy prefix. Deliberately not
     * {@code @PathVariable} — Spring would decode it once, and the path policy needs to see the
     * original encoding to catch double-encoded escapes.
     */
    private static String upstreamPath(HttpServletRequest request) {
        String uri = request.getRequestURI();
        String context = request.getContextPath();
        if (context != null && !context.isEmpty() && uri.startsWith(context)) {
            uri = uri.substring(context.length());
        }
        String remainder = uri.substring(PowerBiProxyService.PROXY_PREFIX.length());
        return remainder.isEmpty() ? "/" : remainder;
    }
}

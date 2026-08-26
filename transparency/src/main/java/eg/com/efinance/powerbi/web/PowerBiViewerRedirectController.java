package eg.com.efinance.powerbi.web;

import eg.com.efinance.powerbi.service.PowerBiProxyService;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

/**
 * Serves the absolute paths the Power BI viewer bundle requests of its own accord.
 *
 * <p>The bundle contains hard-coded root-relative URLs ({@code /api/...}, {@code /Reports/...}) that
 * no amount of HTML rewriting reaches, because they are built in JavaScript at runtime. Rather than
 * redirecting — which costs a round trip per asset and breaks XHR — those paths are proxied in
 * place, through the same policy as every other request.
 *
 * <p>More specific mappings win: {@code /reports/powerbi/transparency} and
 * {@code /reports/powerbi/proxy/**} are matched by their own controllers before {@code /reports/**}
 * is considered.
 */
@RestController
public class PowerBiViewerRedirectController {

    private final PowerBiProxyService proxyService;

    public PowerBiViewerRedirectController(PowerBiProxyService proxyService) {
        this.proxyService = proxyService;
    }

    @RequestMapping(
            value = {
                "/powerbi/**",
                "/PowerBI/**",
                "/ReportServer/**",
                "/Reserved.ReportViewerWebControl.axd",
                "/Reports/**",
                "/reports/**",
                "/api/**",
                "/explore/**",
                "/modelsAndExploration/**",
                "/querydata/**",
                "/metadata/**",
                "/resources/**",
                "/public/**",
                "/13.0.*/**"
            },
            method = {RequestMethod.GET, RequestMethod.HEAD})
    public ResponseEntity<byte[]> proxyViewerResource(HttpServletRequest request) {
        String path = request.getRequestURI();
        String context = request.getContextPath();
        if (context != null && !context.isEmpty() && path.startsWith(context)) {
            path = path.substring(context.length());
        }
        return PowerBiProxyController.respond(proxyService.proxy(path, request));
    }
}

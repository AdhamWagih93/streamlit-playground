package eg.com.efinance.powerbi.web;

import eg.com.efinance.powerbi.config.AppProperties;
import eg.com.efinance.powerbi.service.PowerBiProxyService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.security.Principal;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.view.RedirectView;

/**
 * Serves the viewer shell — the only HTML page this application authors itself.
 *
 * <p>The shell is a thin, same-origin frame host: the chrome, status, controls and recovery states
 * are ours; the report inside the frame is served through the proxy.
 */
@Controller
public class TransparencyViewController {

    public static final String TRANSPARENCY_PATH = "/reports/powerbi/transparency";

    private final PowerBiProxyService proxyService;
    private final AppProperties appProperties;

    public TransparencyViewController(PowerBiProxyService proxyService, AppProperties appProperties) {
        this.proxyService = proxyService;
        this.appProperties = appProperties;
    }

    @GetMapping({"/", "/index.html"})
    public RedirectView index() {
        return new RedirectView(TRANSPARENCY_PATH);
    }

    @GetMapping(value = TRANSPARENCY_PATH, produces = MediaType.TEXT_HTML_VALUE)
    public String transparency(
            HttpServletRequest request,
            HttpServletResponse response,
            Principal principal,
            @RequestParam(name = "theme", required = false) String theme,
            Model model) {

        // The report is the live figure, never a snapshot: no intermediary may hold on to it.
        response.setHeader(HttpHeaders.CACHE_CONTROL, CacheControl.noStore().mustRevalidate().getHeaderValue());

        proxyService.auditReportAccess(request, principal == null ? "anonymous" : principal.getName());

        var ui = appProperties.getUi();
        model.addAttribute("brand", ui.getBrand());
        model.addAttribute("title", ui.getTitle());
        model.addAttribute("reportName", ui.getReportName());
        model.addAttribute("supportContact", ui.getSupportContact());
        model.addAttribute("loadTimeoutMs", ui.getLoadTimeout().toMillis());
        model.addAttribute("frameUrl", proxyService.transparencyFrameUrl());
        // Provenance without disclosure: the report's path on the source system, never its host.
        model.addAttribute("sourceLabel", proxyService.reportSourcePath());
        model.addAttribute("statusUrl", ReportStatusController.STATUS_PATH);
        model.addAttribute("principal", principal == null ? null : principal.getName());
        model.addAttribute("requestId", request.getAttribute(CorrelationIdFilter.ATTRIBUTE));
        model.addAttribute("nonce", request.getAttribute(ContentSecurityPolicyFilter.NONCE_ATTRIBUTE));
        model.addAttribute("theme", pinnedTheme(theme));
        return "transparency";
    }

    /**
     * A theme pinned on the URL, for kiosk and wall displays: those browsers start from an empty
     * profile every morning, so a stored preference never survives to the next day.
     */
    private static String pinnedTheme(String requested) {
        return "light".equals(requested) || "dark".equals(requested) ? requested : null;
    }
}

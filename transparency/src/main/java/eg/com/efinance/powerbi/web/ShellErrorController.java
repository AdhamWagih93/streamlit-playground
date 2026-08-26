package eg.com.efinance.powerbi.web;

import jakarta.servlet.RequestDispatcher;
import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import org.springframework.boot.web.servlet.error.ErrorController;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.servlet.ModelAndView;

/**
 * Container-level errors — anything that fails before or outside a controller, such as a security
 * rejection — end up here. Without it those responses fall back to a blank page, which reads as a
 * broken deployment rather than a denied request.
 */
@Controller
public class ShellErrorController implements ErrorController {

    private static final Map<Integer, String[]> COPY = Map.of(
            401, new String[] {"Sign in to continue", "This dashboard requires an account."},
            403, new String[] {"Access denied", "Your account does not have permission to view this dashboard."},
            404, new String[] {"Not found", "There is nothing at this address."},
            405, new String[] {"Not available", "That action is not supported here."},
            429, new String[] {"Too many requests", "Slow down for a moment, then try again."},
            502, new String[] {"Report server unavailable", "The report server did not respond. Try again shortly."},
            503, new String[] {"Temporarily unavailable", "The service is starting up or under maintenance."});

    private static final String[] FALLBACK = {"Something went wrong", "An unexpected error occurred. The incident has been logged."};

    @RequestMapping(value = "/error", produces = MediaType.TEXT_HTML_VALUE)
    public ModelAndView errorPage(HttpServletRequest request) {
        HttpStatus status = resolveStatus(request);
        String[] copy = COPY.getOrDefault(status.value(), FALLBACK);

        ModelAndView view = new ModelAndView("error");
        view.setStatus(status);
        view.addObject("status", status.value());
        view.addObject("statusText", status.getReasonPhrase());
        view.addObject("title", copy[0]);
        view.addObject("detail", copy[1]);
        view.addObject("retryable", status.is5xxServerError());
        view.addObject("retryUrl", originalPath(request));
        view.addObject("requestId", String.valueOf(request.getAttribute(CorrelationIdFilter.ATTRIBUTE)));
        view.addObject("nonce", request.getAttribute(ContentSecurityPolicyFilter.NONCE_ATTRIBUTE));
        return view;
    }

    @RequestMapping("/error")
    public ResponseEntity<ProblemDetail> errorJson(HttpServletRequest request) {
        HttpStatus status = resolveStatus(request);
        String[] copy = COPY.getOrDefault(status.value(), FALLBACK);

        ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, copy[1]);
        problem.setTitle(copy[0]);
        problem.setProperty("requestId", String.valueOf(request.getAttribute(CorrelationIdFilter.ATTRIBUTE)));
        return ResponseEntity.status(status).contentType(MediaType.APPLICATION_PROBLEM_JSON).body(problem);
    }

    private static HttpStatus resolveStatus(HttpServletRequest request) {
        Object code = request.getAttribute(RequestDispatcher.ERROR_STATUS_CODE);
        if (code instanceof Integer value) {
            HttpStatus resolved = HttpStatus.resolve(value);
            if (resolved != null) {
                return resolved;
            }
        }
        return HttpStatus.INTERNAL_SERVER_ERROR;
    }

    private static String originalPath(HttpServletRequest request) {
        Object uri = request.getAttribute(RequestDispatcher.ERROR_REQUEST_URI);
        return uri == null ? "/" : uri.toString();
    }
}

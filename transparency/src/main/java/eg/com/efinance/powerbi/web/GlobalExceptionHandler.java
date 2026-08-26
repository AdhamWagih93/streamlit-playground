package eg.com.efinance.powerbi.web;

import jakarta.servlet.http.HttpServletRequest;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.servlet.ModelAndView;
import org.springframework.web.servlet.resource.NoResourceFoundException;

/**
 * One place where every failure becomes a response.
 *
 * <p>The response format follows the caller, not the exception: a browser navigating to a broken
 * report gets the designed error page in the same visual language as the dashboard; an XHR from the
 * viewer bundle, or any API client, gets RFC 7807 {@code application/problem+json}. Both carry the
 * correlation id, so a screenshot from a user is enough to find the server-side log line.
 */
@ControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(ForbiddenProxyRequestException.class)
    public Object handleForbiddenProxyRequest(ForbiddenProxyRequestException ex, HttpServletRequest request) {
        log.warn("Blocked proxy request path={} reason={}", ex.getPath(), ex.getMessage());
        return respond(
                request,
                HttpStatus.FORBIDDEN,
                "Resource not available",
                "This resource is not part of the published transparency report.");
    }

    @ExceptionHandler(AccessDeniedException.class)
    public Object handleAccessDenied(AccessDeniedException ex, HttpServletRequest request) {
        log.warn("Access denied for {}: {}", request.getRequestURI(), ex.getMessage());
        return respond(
                request,
                HttpStatus.FORBIDDEN,
                "Access denied",
                "Your account does not have permission to view this dashboard.");
    }

    @ExceptionHandler(ReportServerUnavailableException.class)
    public Object handleReportServerUnavailable(ReportServerUnavailableException ex, HttpServletRequest request) {
        log.error("Report server unavailable for {}: {}", request.getRequestURI(), ex.toString());
        return respond(
                request,
                HttpStatus.BAD_GATEWAY,
                "Report server unavailable",
                "The report server did not respond. This is usually temporary — try again shortly.");
    }

    @ExceptionHandler(NoResourceFoundException.class)
    public Object handleNoResourceFound(NoResourceFoundException ex, HttpServletRequest request) {
        return respond(
                request,
                HttpStatus.NOT_FOUND,
                "Not found",
                "There is nothing at this address.");
    }

    @ExceptionHandler(Exception.class)
    public Object handleUnexpected(Exception ex, HttpServletRequest request) {
        log.error("Unhandled error for {}", request.getRequestURI(), ex);
        return respond(
                request,
                HttpStatus.INTERNAL_SERVER_ERROR,
                "Something went wrong",
                "An unexpected error occurred. The incident has been logged.");
    }

    /** Content negotiation, done on the request the browser actually sent. */
    private Object respond(HttpServletRequest request, HttpStatus status, String title, String detail) {
        String requestId = String.valueOf(request.getAttribute(CorrelationIdFilter.ATTRIBUTE));

        if (!prefersHtml(request)) {
            ProblemDetail problem = ProblemDetail.forStatusAndDetail(status, detail);
            problem.setTitle(title);
            problem.setProperty("requestId", requestId);
            return ResponseEntity.status(status)
                    .contentType(MediaType.APPLICATION_PROBLEM_JSON)
                    .body(problem);
        }

        ModelAndView view = new ModelAndView("error");
        view.setStatus(status);
        view.addObject("status", status.value());
        view.addObject("statusText", status.getReasonPhrase());
        view.addObject("title", title);
        view.addObject("detail", detail);
        view.addObject("requestId", requestId);
        view.addObject("retryable", status.is5xxServerError());
        // Thymeleaf 3.1 removed #httpServletRequest, so the retry target is passed explicitly.
        view.addObject("retryUrl", request.getRequestURI());
        view.addObject("nonce", request.getAttribute(ContentSecurityPolicyFilter.NONCE_ATTRIBUTE));
        return view;
    }

    /**
     * A top-level navigation asks for HTML and is not an XHR. The viewer bundle's own calls carry
     * {@code X-Requested-With} or an {@code Accept} that prefers JSON, and must keep getting JSON —
     * handing them an HTML page turns a clean upstream error into a parse failure.
     */
    private static boolean prefersHtml(HttpServletRequest request) {
        if (request.getHeader("X-Requested-With") != null) {
            return false;
        }
        String accept = request.getHeader(HttpHeaders.ACCEPT);
        if (accept == null || accept.isBlank()) {
            return false;
        }
        List<MediaType> accepted;
        try {
            accepted = MediaType.parseMediaTypes(accept);
        } catch (org.springframework.http.InvalidMediaTypeException ex) {
            return false;
        }
        for (MediaType type : accepted) {
            if (type.includes(MediaType.TEXT_HTML)) {
                return true;
            }
            if (type.includes(MediaType.APPLICATION_JSON) || type.getSubtype().endsWith("+json")) {
                return false;
            }
        }
        return false;
    }
}

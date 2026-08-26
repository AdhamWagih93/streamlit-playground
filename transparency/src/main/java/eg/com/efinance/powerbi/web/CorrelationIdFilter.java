package eg.com.efinance.powerbi.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.MDC;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Stamps every request with a correlation id so a user-visible error, an audit line and a server
 * log entry can be tied together without guesswork.
 */
public class CorrelationIdFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Request-Id";
    public static final String ATTRIBUTE = "requestId";
    public static final String MDC_KEY = "requestId";

    private static final int MAX_INBOUND_LENGTH = 64;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        String requestId = sanitize(request.getHeader(HEADER));
        if (requestId == null) {
            requestId = UUID.randomUUID().toString();
        }

        request.setAttribute(ATTRIBUTE, requestId);
        response.setHeader(HEADER, requestId);
        MDC.put(MDC_KEY, requestId);
        try {
            chain.doFilter(request, response);
        } finally {
            MDC.remove(MDC_KEY);
        }
    }

    /** Inbound ids are echoed into logs and headers, so only a conservative charset is accepted. */
    private static String sanitize(String candidate) {
        if (candidate == null || candidate.isBlank() || candidate.length() > MAX_INBOUND_LENGTH) {
            return null;
        }
        for (int i = 0; i < candidate.length(); i++) {
            char c = candidate.charAt(i);
            boolean allowed = Character.isLetterOrDigit(c) || c == '-' || c == '_' || c == '.';
            if (!allowed) {
                return null;
            }
        }
        return candidate;
    }

    @Override
    protected boolean shouldNotFilterErrorDispatch() {
        return false;
    }
}

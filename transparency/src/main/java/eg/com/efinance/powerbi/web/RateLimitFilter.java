package eg.com.efinance.powerbi.web;

import eg.com.efinance.powerbi.config.AppProperties;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Per-client token bucket in front of the proxy.
 *
 * <p>One misbehaving viewer session can otherwise drain the upstream connection pool and take the
 * dashboard down for everyone; the bucket turns that into a localised 429. Sized for a browser
 * loading a report (hundreds of asset requests in a burst), not for an API.
 */
public class RateLimitFilter extends OncePerRequestFilter {

    private static final class Bucket {
        final AtomicLong tokensMilli;
        volatile long lastRefillNanos;

        Bucket(long initialTokensMilli, long nowNanos) {
            this.tokensMilli = new AtomicLong(initialTokensMilli);
            this.lastRefillNanos = nowNanos;
        }
    }

    private final AppProperties.RateLimit config;
    private final ProxyPathMatcher proxyPaths;
    private final Map<String, Bucket> buckets = new ConcurrentHashMap<>();
    private final long capacityMilli;
    private final double tokensPerNano;

    public RateLimitFilter(AppProperties.RateLimit config, ProxyPathMatcher proxyPaths) {
        this.config = config;
        this.proxyPaths = proxyPaths;
        this.capacityMilli = (long) config.getBurst() * 1000L;
        this.tokensPerNano = config.getRequestsPerMinute() / 60_000_000_000.0;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {

        if (config.isEnabled() && proxyPaths.isProxied(request.getRequestURI()) && !tryConsume(clientKey(request))) {
            response.setStatus(429);
            response.setContentType("application/problem+json");
            response.setHeader("Retry-After", "5");
            response.getWriter()
                    .write("{\"type\":\"about:blank\",\"title\":\"Too Many Requests\",\"status\":429,"
                            + "\"detail\":\"Slow down and retry in a few seconds.\"}");
            return;
        }
        chain.doFilter(request, response);
    }

    private boolean tryConsume(String key) {
        long now = System.nanoTime();
        Bucket bucket = buckets.computeIfAbsent(key, k -> new Bucket(capacityMilli, now));

        long elapsed = now - bucket.lastRefillNanos;
        if (elapsed > 0) {
            bucket.lastRefillNanos = now;
            long refill = (long) (elapsed * tokensPerNano * 1000.0);
            if (refill > 0) {
                bucket.tokensMilli.updateAndGet(current -> Math.min(capacityMilli, current + refill));
            }
        }

        // Buckets are cheap but unbounded growth is not; prune when the map gets unreasonable.
        if (buckets.size() > 10_000) {
            buckets.entrySet().removeIf(entry -> entry.getValue().tokensMilli.get() >= capacityMilli);
        }

        return bucket.tokensMilli.getAndUpdate(current -> current >= 1000 ? current - 1000 : current) >= 1000;
    }

    private static String clientKey(HttpServletRequest request) {
        java.security.Principal principal = request.getUserPrincipal();
        if (principal != null && principal.getName() != null) {
            return "user:" + principal.getName();
        }
        return "ip:" + request.getRemoteAddr();
    }

    @Override
    protected boolean shouldNotFilterAsyncDispatch() {
        return true;
    }
}

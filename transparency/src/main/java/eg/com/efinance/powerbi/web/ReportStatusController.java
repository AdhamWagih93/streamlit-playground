package eg.com.efinance.powerbi.web;

import eg.com.efinance.powerbi.config.PowerBiReportProperties;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.apache.hc.client5.http.classic.methods.HttpHead;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.springframework.http.CacheControl;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Upstream reachability for the shell's status indicator.
 *
 * <p>Answers are cached briefly so an open dashboard polling every few seconds cannot turn into a
 * health-check flood against the Report Server.
 */
@RestController
public class ReportStatusController {

    public static final String STATUS_PATH = "/reports/powerbi/status";

    private static final Duration CACHE_TTL = Duration.ofSeconds(10);

    private record Snapshot(Instant takenAt, boolean reachable, long latencyMs, String detail) {}

    private final CloseableHttpClient httpClient;
    private final PowerBiReportProperties properties;
    private final AtomicReference<Snapshot> cache = new AtomicReference<>();

    public ReportStatusController(CloseableHttpClient httpClient, PowerBiReportProperties properties) {
        this.httpClient = httpClient;
        this.properties = properties;
    }

    @GetMapping(STATUS_PATH)
    public ResponseEntity<Map<String, Object>> status() {
        Snapshot snapshot = cache.get();
        if (snapshot == null || Duration.between(snapshot.takenAt(), Instant.now()).compareTo(CACHE_TTL) > 0) {
            snapshot = probe();
            cache.set(snapshot);
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("reachable", snapshot.reachable());
        body.put("latencyMs", snapshot.latencyMs());
        body.put("detail", snapshot.detail());
        body.put("checkedAt", snapshot.takenAt().toString());

        return ResponseEntity.ok()
                .cacheControl(CacheControl.noStore())
                .body(body);
    }

    private Snapshot probe() {
        long started = System.nanoTime();
        try {
            var request = new HttpHead(properties.resolvedReportUrl());
            return httpClient.execute(request, response -> {
                long elapsed = (System.nanoTime() - started) / 1_000_000L;
                boolean ok = response.getCode() < 500;
                return new Snapshot(Instant.now(), ok, elapsed, ok ? "Connected" : "Report server error");
            });
        } catch (Exception ex) {
            long elapsed = (System.nanoTime() - started) / 1_000_000L;
            return new Snapshot(Instant.now(), false, elapsed, "Report server unreachable");
        }
    }
}

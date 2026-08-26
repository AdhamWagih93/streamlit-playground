package eg.com.efinance.powerbi.web;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Absorbs the two requests every browser makes unprompted, so they never reach the proxy and never
 * fill the logs with 404s.
 */
@RestController
public class FaviconController {

    /**
     * The shell links a vector icon ({@code /img/favicon.svg}); this legacy path stays a 204 so an
     * unadorned {@code /favicon.ico} request is never mistaken for a report resource.
     */
    @GetMapping("/favicon.ico")
    public ResponseEntity<Void> favicon() {
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/.well-known/appspecific/com.chrome.devtools.json")
    public ResponseEntity<Void> devToolsMetadata() {
        return ResponseEntity.noContent().build();
    }
}

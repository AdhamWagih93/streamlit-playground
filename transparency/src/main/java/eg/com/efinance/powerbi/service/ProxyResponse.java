package eg.com.efinance.powerbi.service;

import org.springframework.http.HttpHeaders;

/**
 * An upstream response after filtering, sanitising and rewriting — everything the controller needs
 * and nothing that ties it to the HTTP client library.
 */
public record ProxyResponse(int status, HttpHeaders headers, byte[] body) {

    public static ProxyResponse of(int status, HttpHeaders headers, byte[] body) {
        return new ProxyResponse(status, headers, body == null ? new byte[0] : body);
    }
}

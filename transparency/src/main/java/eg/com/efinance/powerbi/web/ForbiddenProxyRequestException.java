package eg.com.efinance.powerbi.web;

/** Thrown when a requested upstream path is outside the published surface of the report. */
public class ForbiddenProxyRequestException extends RuntimeException {

    private final String path;

    public ForbiddenProxyRequestException(String path, String reason) {
        super(reason);
        this.path = path;
    }

    public String getPath() {
        return path;
    }
}

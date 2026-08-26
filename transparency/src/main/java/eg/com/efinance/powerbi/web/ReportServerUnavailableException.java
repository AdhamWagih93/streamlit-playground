package eg.com.efinance.powerbi.web;

/** Thrown when the upstream Power BI Report Server cannot be reached or answers unusably. */
public class ReportServerUnavailableException extends RuntimeException {

    public ReportServerUnavailableException(String message, Throwable cause) {
        super(message, cause);
    }

    public ReportServerUnavailableException(String message) {
        super(message);
    }
}

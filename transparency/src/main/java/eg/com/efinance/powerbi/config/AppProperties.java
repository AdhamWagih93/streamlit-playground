package eg.com.efinance.powerbi.config;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Application-level switches and the copy shown in the viewer shell.
 *
 * <p>Everything a deployment might want to rebrand or retitle lives here, so the templates stay
 * free of hard-coded organisation names.
 */
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private final Security security = new Security();
    private final Ui ui = new Ui();
    private final RateLimit rateLimit = new RateLimit();

    public static class Security {
        /** When false, every endpoint is permitted — local testing only. */
        private boolean enabled = true;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }
    }

    public static class Ui {
        private String brand = "Transparency";
        private String title = "Transparency Dashboard";
        private String reportName = "Transparency Report";
        private String supportContact = "";
        /** Seconds the shell waits for the report frame before showing the recovery state. */
        private Duration loadTimeout = Duration.ofSeconds(45);

        public String getBrand() {
            return brand;
        }

        public void setBrand(String brand) {
            this.brand = brand;
        }

        public String getTitle() {
            return title;
        }

        public void setTitle(String title) {
            this.title = title;
        }

        public String getReportName() {
            return reportName;
        }

        public void setReportName(String reportName) {
            this.reportName = reportName;
        }

        public String getSupportContact() {
            return supportContact;
        }

        public void setSupportContact(String supportContact) {
            this.supportContact = supportContact;
        }

        public Duration getLoadTimeout() {
            return loadTimeout;
        }

        public void setLoadTimeout(Duration loadTimeout) {
            this.loadTimeout = loadTimeout;
        }
    }

    public static class RateLimit {
        private boolean enabled = true;
        /** Sustained requests per minute per client, refilled continuously. */
        private int requestsPerMinute = 600;
        /** Burst allowance on top of the sustained rate. */
        private int burst = 120;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }

        public int getRequestsPerMinute() {
            return requestsPerMinute;
        }

        public void setRequestsPerMinute(int requestsPerMinute) {
            this.requestsPerMinute = requestsPerMinute;
        }

        public int getBurst() {
            return burst;
        }

        public void setBurst(int burst) {
            this.burst = burst;
        }
    }

    public Security getSecurity() {
        return security;
    }

    public Ui getUi() {
        return ui;
    }

    public RateLimit getRateLimit() {
        return rateLimit;
    }
}

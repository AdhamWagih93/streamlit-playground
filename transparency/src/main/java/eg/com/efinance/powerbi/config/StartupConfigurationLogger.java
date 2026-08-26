package eg.com.efinance.powerbi.config;

import eg.com.efinance.powerbi.config.PowerBiReportProperties.AuthType;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

/**
 * Logs the effective upstream configuration once the context is ready.
 *
 * <p>Presence, never value: an operator needs to know whether a credential was picked up, not what
 * it is. Passwords are not logged at any level.
 */
@Component
public class StartupConfigurationLogger {

    private static final Logger log = LoggerFactory.getLogger(StartupConfigurationLogger.class);

    private final PowerBiReportProperties properties;
    private final AppProperties appProperties;

    public StartupConfigurationLogger(PowerBiReportProperties properties, AppProperties appProperties) {
        this.properties = properties;
        this.appProperties = appProperties;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void logConfiguration() {
        log.info("Power BI proxy configuration");
        log.info("  baseUrl                : {}", properties.getBaseUrl());
        log.info("  allowedHost            : {}", properties.getAllowedHost());
        log.info("  reportUrl              : {}", properties.resolvedReportUrl());
        log.info("  transparencyReportPath : {}", properties.getTransparencyReportPath());
        log.info("  authType               : {}", properties.getAuthType());
        log.info("  username configured    : {}", isSet(properties.getUsername()));
        log.info("  password configured    : {}", isSet(properties.getPassword()));
        log.info("  connectTimeout         : {}", properties.getConnectTimeout());
        log.info("  responseTimeout        : {}", properties.getResponseTimeout());
        log.info("  pool                   : {} total / {} per route",
                properties.getPool().getMaxTotal(), properties.getPool().getMaxPerRoute());
        log.info("  allowed path prefixes  : {}", properties.getAllowedResourcePathPrefixes());

        if (properties.getAuthType() == AuthType.NTLM) {
            log.info("  ntlm domain            : {}", orNone(properties.resolvedDomain()));
            log.info("  ntlm username          : {}", orNone(properties.resolvedUsername()));
            log.info("  ntlm workstation       : {}", orNone(properties.getWorkstation()));
        }

        boolean securityEnabled = appProperties.getSecurity().isEnabled();
        log.info("  application security   : {}", securityEnabled ? "enabled" : "DISABLED");
        if (!securityEnabled) {
            log.warn("Application security is disabled — every endpoint is public. "
                    + "Set app.security.enabled=true for any non-local deployment.");
        }
        if (properties.getAuthType() == AuthType.NONE) {
            log.warn("Upstream authentication is NONE — intended for local testing only.");
        }
    }

    private static String isSet(String value) {
        return value != null && !value.isBlank() ? "yes" : "no";
    }

    private static String orNone(String value) {
        return value != null && !value.isBlank() ? value : "(none)";
    }
}

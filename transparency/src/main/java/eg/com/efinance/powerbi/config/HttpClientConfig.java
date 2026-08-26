package eg.com.efinance.powerbi.config;

import eg.com.efinance.powerbi.config.PowerBiReportProperties.AuthType;
import java.net.URI;
import java.util.concurrent.TimeUnit;
import org.apache.hc.client5.http.auth.AuthScope;
import org.apache.hc.client5.http.auth.Credentials;
import org.apache.hc.client5.http.auth.NTCredentials;
import org.apache.hc.client5.http.auth.UsernamePasswordCredentials;
import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.auth.BasicCredentialsProvider;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.core5.http.HttpHost;
import org.apache.hc.core5.util.TimeValue;
import org.apache.hc.core5.util.Timeout;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Builds the single pooled {@link CloseableHttpClient} used for every upstream call.
 *
 * <p>The pool is deliberately not shared: NTLM authenticates the <em>connection</em>, not the
 * request, so auth state must stay pinned to a leased connection across the handshake.
 */
@Configuration(proxyBeanMethods = false)
public class HttpClientConfig {

    @Bean(destroyMethod = "close")
    public PoolingHttpClientConnectionManager reportServerConnectionManager(PowerBiReportProperties properties) {
        var pool = properties.getPool();
        return PoolingHttpClientConnectionManagerBuilder.create()
                .setMaxConnTotal(pool.getMaxTotal())
                .setMaxConnPerRoute(pool.getMaxPerRoute())
                .setValidateAfterInactivity(TimeValue.ofMilliseconds(pool.getValidateAfterInactivity().toMillis()))
                .setDefaultConnectionConfig(ConnectionConfig.custom()
                        .setConnectTimeout(Timeout.ofMilliseconds(properties.getConnectTimeout().toMillis()))
                        .setTimeToLive(TimeValue.ofMinutes(10))
                        .build())
                .build();
    }

    @Bean(destroyMethod = "close")
    public CloseableHttpClient reportServerHttpClient(
            PowerBiReportProperties properties, PoolingHttpClientConnectionManager connectionManager) {

        RequestConfig requestConfig = RequestConfig.custom()
                .setConnectionRequestTimeout(Timeout.ofMilliseconds(properties.getConnectTimeout().toMillis()))
                .setResponseTimeout(Timeout.ofMilliseconds(properties.getResponseTimeout().toMillis()))
                .setRedirectsEnabled(false)
                .setExpectContinueEnabled(false)
                .build();

        var builder = HttpClients.custom()
                .setConnectionManager(connectionManager)
                .setConnectionManagerShared(false)
                .setDefaultRequestConfig(requestConfig)
                .evictIdleConnections(TimeValue.of(properties.getPool().getIdleEviction().toSeconds(), TimeUnit.SECONDS))
                .evictExpiredConnections()
                .disableCookieManagement()
                .disableAutomaticRetries()
                .disableRedirectHandling();

        credentials(properties).ifPresent(credentials -> {
            URI base = URI.create(properties.getBaseUrl());
            HttpHost host = new HttpHost(base.getScheme(), base.getHost(), base.getPort());
            var provider = new BasicCredentialsProvider();
            provider.setCredentials(new AuthScope(host), credentials);
            builder.setDefaultCredentialsProvider(provider);
        });

        return builder.build();
    }

    private static java.util.Optional<Credentials> credentials(PowerBiReportProperties properties) {
        String username = properties.resolvedUsername();
        char[] password = properties.getPassword() == null ? new char[0] : properties.getPassword().toCharArray();

        if (properties.getAuthType() == AuthType.NONE || username == null || username.isBlank()) {
            return java.util.Optional.empty();
        }
        if (properties.getAuthType() == AuthType.NTLM) {
            return java.util.Optional.of(new NTCredentials(
                    username, password, properties.getWorkstation(), properties.resolvedDomain()));
        }
        return java.util.Optional.of(new UsernamePasswordCredentials(username, password));
    }
}

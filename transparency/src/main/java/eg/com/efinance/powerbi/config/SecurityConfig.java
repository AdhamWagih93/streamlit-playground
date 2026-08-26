package eg.com.efinance.powerbi.config;

import eg.com.efinance.powerbi.web.ContentSecurityPolicyFilter;
import eg.com.efinance.powerbi.web.CorrelationIdFilter;
import eg.com.efinance.powerbi.web.ProxyPathMatcher;
import eg.com.efinance.powerbi.web.RateLimitFilter;
import org.springframework.boot.autoconfigure.security.SecurityProperties;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;
import org.springframework.security.web.header.writers.ReferrerPolicyHeaderWriter;
import org.springframework.security.web.header.writers.XXssProtectionHeaderWriter;
import org.springframework.security.web.util.matcher.AntPathRequestMatcher;
import org.springframework.security.web.util.matcher.OrRequestMatcher;
import org.springframework.security.web.util.matcher.RequestMatcher;

@Configuration(proxyBeanMethods = false)
@EnableWebSecurity
public class SecurityConfig {

    /** Endpoints that must stay reachable without a session: health, favicon, the landing redirect. */
    private static final String[] PUBLIC_PATHS = {
        "/",
        "/index.html",
        "/favicon.ico",
        "/.well-known/appspecific/com.chrome.devtools.json",
        "/actuator/health",
        "/actuator/health/**",
        "/actuator/info",
        "/error",
        "/css/**",
        "/js/**",
        "/img/**"
    };

    @Bean
    public ProxyPathMatcher proxyPathMatcher() {
        return ProxyPathMatcher.create();
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http, AppProperties appProperties) throws Exception {
        RequestMatcher proxyMatcher = new OrRequestMatcher(ProxyPathMatcher.PROXY_PATTERNS.stream()
                .map(pattern -> (RequestMatcher) AntPathRequestMatcher.antMatcher(pattern))
                .toList());

        http.csrf(csrf -> csrf.csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse())
                // The upstream viewer drives its own requests and cannot carry our token; those
                // paths are read-only GET/HEAD proxies, so the exemption does not widen state change.
                .ignoringRequestMatchers(proxyMatcher)
                .ignoringRequestMatchers(AntPathRequestMatcher.antMatcher("/actuator/health/**"))
                .ignoringRequestMatchers(AntPathRequestMatcher.antMatcher("/favicon.ico"))
                .ignoringRequestMatchers(
                        AntPathRequestMatcher.antMatcher("/.well-known/appspecific/com.chrome.devtools.json")));

        http.headers(headers -> headers
                // CSP is written per-request by ContentSecurityPolicyFilter (nonce + policy split).
                .contentSecurityPolicy(csp -> csp.policyDirectives("default-src 'self'"))
                .frameOptions(frame -> frame.sameOrigin())
                .httpStrictTransportSecurity(Customizer.withDefaults())
                .referrerPolicy(referrer -> referrer.policy(ReferrerPolicyHeaderWriter.ReferrerPolicy.SAME_ORIGIN))
                .xssProtection(xss -> xss.headerValue(XXssProtectionHeaderWriter.HeaderValue.DISABLED))
                .permissionsPolicy(permissions ->
                        permissions.policy("geolocation=(), microphone=(), camera=(), payment=(), usb=()")));

        http.authorizeHttpRequests(requests -> {
            requests.requestMatchers(java.util.Arrays.stream(PUBLIC_PATHS)
                            .map(AntPathRequestMatcher::antMatcher)
                            .toArray(RequestMatcher[]::new))
                    .permitAll();
            if (appProperties.getSecurity().isEnabled()) {
                requests.anyRequest().authenticated();
            } else {
                requests.anyRequest().permitAll();
            }
        });

        if (appProperties.getSecurity().isEnabled()) {
            http.httpBasic(Customizer.withDefaults());
            http.formLogin(Customizer.withDefaults());
        }

        return http.build();
    }

    @Bean
    public FilterRegistrationBean<CorrelationIdFilter> correlationIdFilter() {
        var registration = new FilterRegistrationBean<>(new CorrelationIdFilter());
        registration.setOrder(SecurityProperties.DEFAULT_FILTER_ORDER - 20);
        return registration;
    }

    @Bean
    public FilterRegistrationBean<ContentSecurityPolicyFilter> contentSecurityPolicyFilter(ProxyPathMatcher matcher) {
        var registration = new FilterRegistrationBean<>(new ContentSecurityPolicyFilter(matcher));
        registration.setOrder(SecurityProperties.DEFAULT_FILTER_ORDER - 10);
        return registration;
    }

    @Bean
    public FilterRegistrationBean<RateLimitFilter> rateLimitFilter(AppProperties properties, ProxyPathMatcher matcher) {
        var registration = new FilterRegistrationBean<>(new RateLimitFilter(properties.getRateLimit(), matcher));
        registration.setOrder(SecurityProperties.DEFAULT_FILTER_ORDER + 10);
        return registration;
    }
}

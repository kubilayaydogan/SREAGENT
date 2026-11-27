package com.lab.air.chaos;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;

/**
 * Panel ve API için HTTP Basic. Yalnız health ve prometheus açık (izleme).
 * chaos.auth.enabled=true iken parola boşsa tüm istekler reddedilir (fail-closed).
 */
@Configuration(proxyBeanMethods = false)
public class BasicAuthFilter {

    @Bean
    FilterRegistrationBean<OncePerRequestFilter> chaosBasicAuth(ChaosProperties props) {
        var auth = props.auth();
        byte[] expected = (auth.user() + ":" + (auth.password() == null ? "" : auth.password()))
                .getBytes(StandardCharsets.UTF_8);
        boolean passwordSet = auth.password() != null && auth.password().length() >= 8;
        OncePerRequestFilter f = new OncePerRequestFilter() {
            @Override
            protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
                    throws ServletException, IOException {
                String p = req.getRequestURI();
                if (!auth.enabled() || p.equals("/actuator/health") || p.startsWith("/actuator/health/")
                        || p.equals("/actuator/prometheus") || p.equals("/lab.css")) {
                    chain.doFilter(req, res);
                    return;
                }
                if (!passwordSet) {
                    res.sendError(503, "CHAOS_PASSWORD tanımlı değil (en az 8 karakter)");
                    return;
                }
                String h = req.getHeader("Authorization");
                if (h != null && h.startsWith("Basic ")) {
                    byte[] given;
                    try {
                        given = Base64.getDecoder().decode(h.substring(6).trim());
                    } catch (IllegalArgumentException e) {
                        given = new byte[0];
                    }
                    if (MessageDigest.isEqual(expected, given)) {
                        chain.doFilter(req, res);
                        return;
                    }
                }
                res.setHeader("WWW-Authenticate", "Basic realm=\"chaos-controller\", charset=\"UTF-8\"");
                res.sendError(401);
            }
        };
        var reg = new FilterRegistrationBean<>(f);
        reg.setOrder(0);
        return reg;
    }
}

package com.lab.air.common.lab;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.servlet.HandlerInterceptor;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

/**
 * /lab/** uçları için kimlik doğrulama (X-Lab-Token, sabit zamanlı karşılaştırma)
 * ve kaynak IP allowlist'i. Proxy başlıklarına (X-Forwarded-For) güvenilmez.
 */
public class LabAccessInterceptor implements HandlerInterceptor {

    public static final String TOKEN_HEADER = "X-Lab-Token";

    private final byte[] token;
    private final List<Cidr> allowed;

    public LabAccessInterceptor(LabProperties p) {
        this.token = p.token() == null ? new byte[0] : p.token().getBytes(StandardCharsets.UTF_8);
        this.allowed = p.allowedCidrs().stream().map(Cidr::parse).toList();
    }

    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse res, Object handler) throws Exception {
        if (token.length < 16) {
            res.sendError(503, "lab token yapılandırılmamış (en az 16 karakter)");
            return false;
        }
        if (!ipAllowed(req.getRemoteAddr())) {
            res.sendError(403, "kaynak IP allowlist dışında");
            return false;
        }
        String given = req.getHeader(TOKEN_HEADER);
        if (given == null || !MessageDigest.isEqual(token, given.getBytes(StandardCharsets.UTF_8))) {
            res.sendError(401, "geçersiz lab token");
            return false;
        }
        return true;
    }

    boolean ipAllowed(String ip) {
        return allowed.stream().anyMatch(c -> c.contains(ip));
    }

    record Cidr(byte[] net, int bits) {
        static Cidr parse(String s) {
            String[] p = s.trim().split("/");
            try {
                byte[] a = InetAddress.getByName(p[0]).getAddress();
                int bits = p.length > 1 ? Integer.parseInt(p[1]) : a.length * 8;
                return new Cidr(a, bits);
            } catch (UnknownHostException e) {
                throw new IllegalArgumentException("geçersiz CIDR: " + s, e);
            }
        }

        boolean contains(String ip) {
            byte[] a;
            try {
                a = InetAddress.getByName(ip).getAddress();
            } catch (UnknownHostException e) {
                return false;
            }
            if (a.length != net.length) {
                return false;
            }
            int full = bits / 8;
            int rem = bits % 8;
            for (int i = 0; i < full; i++) {
                if (a[i] != net[i]) {
                    return false;
                }
            }
            if (rem == 0) {
                return true;
            }
            int mask = (0xFF << (8 - rem)) & 0xFF;
            return (a[full] & mask) == (net[full] & mask);
        }
    }
}

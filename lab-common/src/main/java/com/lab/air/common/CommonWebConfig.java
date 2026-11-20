package com.lab.air.common;

import com.lab.air.common.lab.LabConfig;
import io.micrometer.common.KeyValue;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.core.Ordered;
import org.springframework.http.server.observation.DefaultServerRequestObservationConvention;
import org.springframework.http.server.observation.ServerRequestObservationContext;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Dört iş uygulamasının ortak web yapılandırması. Uygulamalar @Import ile alır. */
@Configuration(proxyBeanMethods = false)
@Import({ApiExceptionHandler.class, LabConfig.class})
public class CommonWebConfig implements WebMvcConfigurer {

    private final String serviceName;

    public CommonWebConfig(@Value("${spring.application.name}") String serviceName) {
        this.serviceName = serviceName;
        SqlTag.setApp(serviceName);
    }

    @Bean
    FilterRegistrationBean<RequestContextFilter> labRequestContextFilter(
            @Value("${spring.datasource.username:unknown}") String dbRole) {
        var reg = new FilterRegistrationBean<>(new RequestContextFilter(dbRole));
        reg.setOrder(Ordered.HIGHEST_PRECEDENCE + 10);
        return reg;
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new OpInterceptor());
    }

    /**
     * http.server.requests metriğine düşük cardinality'li "operation" etiketi ekler
     * (ör. create_booking). request_id/experiment_id EKLENMEZ.
     */
    @Bean
    DefaultServerRequestObservationConvention operationTagConvention() {
        return new DefaultServerRequestObservationConvention() {
            @Override
            public io.micrometer.common.KeyValues getLowCardinalityKeyValues(ServerRequestObservationContext ctx) {
                Object op = ctx.getCarrier() == null ? null : ctx.getCarrier().getAttribute(OpInterceptor.ATTR);
                return super.getLowCardinalityKeyValues(ctx)
                        .and(KeyValue.of("operation", op == null ? "none" : op.toString()));
            }
        };
    }
}

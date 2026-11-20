package com.lab.air.common.lab;

import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import javax.sql.DataSource;

@Configuration(proxyBeanMethods = false)
@EnableConfigurationProperties(LabProperties.class)
public class LabConfig {

    @Bean
    LabHolds labHolds(LabProperties props) {
        return new LabHolds(props);
    }

    /** Lab uçları yalnız lab.enabled=true iken (lab profili) yüklenir. */
    @Configuration(proxyBeanMethods = false)
    @ConditionalOnProperty(prefix = "lab", name = "enabled", havingValue = "true")
    static class Enabled implements WebMvcConfigurer {

        private final LabProperties props;

        Enabled(LabProperties props) {
            this.props = props;
        }

        @Override
        public void addInterceptors(InterceptorRegistry registry) {
            registry.addInterceptor(new LabAccessInterceptor(props)).addPathPatterns("/lab/**");
        }

        @Bean(destroyMethod = "close")
        LabPressurePool labPressurePool(Environment env, ObjectProvider<MeterRegistry> registry) {
            return new LabPressurePool(env, props, registry.getIfAvailable());
        }

        @Bean
        LabController labController(DataSource ds, LabPressurePool pressure, LabHolds holds, Environment env) {
            return new LabController(ds, pressure, holds, props, env);
        }
    }
}

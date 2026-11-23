package com.lab.air.chaos;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.EnableConfigurationProperties;

/**
 * chaos-controller: dört iş uygulamasına YALNIZ HTTP API üzerinden yük verir.
 * Veritabanı sürücüsü bağımlılığı yoktur; DB'ye bağlanamaz.
 */
@SpringBootApplication
@EnableConfigurationProperties({ChaosProperties.class, com.lab.air.chaos.v2.V2Properties.class})
public class ChaosApplication {

    public static void main(String[] args) {
        SpringApplication.run(ChaosApplication.class, args);
    }
}

package com.lab.air.flightops;

import com.lab.air.common.CommonWebConfig;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;

@SpringBootApplication
@Import(CommonWebConfig.class)
public class FlightOpsApplication {

    public static void main(String[] args) {
        SpringApplication.run(FlightOpsApplication.class, args);
    }
}

package com.lab.air.checkin;

import com.lab.air.common.CommonWebConfig;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Import;

@SpringBootApplication
@Import(CommonWebConfig.class)
public class CheckinApplication {

    public static void main(String[] args) {
        SpringApplication.run(CheckinApplication.class, args);
    }
}

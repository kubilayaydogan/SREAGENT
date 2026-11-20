package com.lab.air.flightops;

import com.lab.air.common.BusinessException;
import com.lab.air.common.Op;
import com.lab.air.flightops.Model.LabHoldRequest;
import com.lab.air.flightops.Model.Result;
import jakarta.validation.Valid;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.concurrent.ThreadLocalRandom;

/**
 * Üçüncü kilit kaynağı: FlightOps gerçek kapasite güncelleme yolunda (kapasite
 * değeri korunarak) envanter satırını sınırlı süre tutar.
 */
@RestController
@RequestMapping("/lab")
@ConditionalOnProperty(prefix = "lab", name = "enabled", havingValue = "true")
public class FlightOpsLabController {

    private final FlightOpsService service;
    private final FlightOpsRepository repo;

    public FlightOpsLabController(FlightOpsService service, FlightOpsRepository repo) {
        this.service = service;
        this.repo = repo;
    }

    @Op("lab_hold_capacity")
    @PostMapping("/flights/capacity-hold")
    public Result hold(@Valid @RequestBody LabHoldRequest req) {
        var flight = req.flightId() != null
                ? repo.get(req.flightId()).orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok"))
                : randomHot();
        return service.updateCapacity(flight.flightId(), flight.capacity(), req.holdMs());
    }

    private Model.Flight randomHot() {
        var hot = repo.hot();
        if (hot.isEmpty()) {
            throw BusinessException.conflict("NO_HOT_FLIGHT", "sıcak uçuş yok");
        }
        return hot.get(ThreadLocalRandom.current().nextInt(hot.size()));
    }
}

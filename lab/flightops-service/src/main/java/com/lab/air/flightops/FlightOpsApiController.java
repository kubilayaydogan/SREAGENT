package com.lab.air.flightops;

import com.lab.air.common.BusinessException;
import com.lab.air.common.Op;
import com.lab.air.flightops.Model.CapacityUpdate;
import com.lab.air.flightops.Model.Flight;
import com.lab.air.flightops.Model.FlightDetail;
import com.lab.air.flightops.Model.LoadFactor;
import com.lab.air.flightops.Model.Result;
import com.lab.air.flightops.Model.StatusUpdate;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api")
public class FlightOpsApiController {

    private final FlightOpsRepository repo;
    private final FlightOpsService service;

    public FlightOpsApiController(FlightOpsRepository repo, FlightOpsService service) {
        this.repo = repo;
        this.service = service;
    }

    @Op("list_flights")
    @GetMapping("/flights")
    public List<Flight> list(@RequestParam(required = false) String status,
                             @RequestParam(defaultValue = "48") int hoursAhead,
                             @RequestParam(defaultValue = "50") int limit) {
        if (status != null && !FlightOpsService.TRANSITIONS.containsKey(status)) {
            throw new IllegalArgumentException("geçersiz status");
        }
        return repo.list(status, clamp(hoursAhead, 1, 24 * 60), clamp(limit, 1, 200));
    }

    @Op("get_flight")
    @GetMapping("/flights/{id}")
    public FlightDetail get(@PathVariable long id) {
        var f = repo.get(id).orElseThrow(() -> BusinessException.notFound("FLIGHT_NOT_FOUND", "uçuş yok"));
        return new FlightDetail(f, repo.events(id, 20));
    }

    @Op("hot_flights")
    @GetMapping("/flights/hot")
    public List<Flight> hot() {
        return repo.hot();
    }

    @Op("sample_flights")
    @GetMapping("/flights/sample")
    public List<Long> sample(@RequestParam(defaultValue = "50") int limit) {
        return repo.sampleIds(clamp(limit, 1, 500));
    }

    @Op("update_status")
    @PostMapping("/flights/{id}/status")
    public Result status(@PathVariable long id, @Valid @RequestBody StatusUpdate req) {
        return service.updateStatus(id, req);
    }

    @Op("update_capacity")
    @PostMapping("/flights/{id}/capacity")
    public Result capacity(@PathVariable long id, @Valid @RequestBody CapacityUpdate req) {
        return service.updateCapacity(id, req.capacity(), 0);
    }

    @Op("load_factor_report")
    @GetMapping("/reports/load-factor")
    public List<LoadFactor> loadFactor(@RequestParam(defaultValue = "7") int days,
                                       @RequestParam(defaultValue = "50") int limit) {
        return repo.loadFactor(clamp(days, 1, 365), clamp(limit, 1, 500));
    }

    static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(max, v));
    }
}

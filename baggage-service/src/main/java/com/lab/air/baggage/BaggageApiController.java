package com.lab.air.baggage;

import com.lab.air.baggage.Model.AddEvent;
import com.lab.air.baggage.Model.Bag;
import com.lab.air.baggage.Model.BagDetail;
import com.lab.air.baggage.Model.HistoryRow;
import com.lab.air.baggage.Model.RegisterBag;
import com.lab.air.baggage.Model.Result;
import com.lab.air.common.Op;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.regex.Pattern;

@RestController
@RequestMapping("/api")
public class BaggageApiController {

    static final Pattern IDEMPOTENCY_KEY = Pattern.compile("[A-Za-z0-9._:-]{8,64}");

    private final BaggageService service;
    private final BaggageRepository repo;

    public BaggageApiController(BaggageService service, BaggageRepository repo) {
        this.service = service;
        this.repo = repo;
    }

    @Op("register_bag")
    @PostMapping("/bags")
    public ResponseEntity<Result> register(@Valid @RequestBody RegisterBag req,
                                           @RequestHeader(value = "Idempotency-Key", required = false) String key) {
        Result r = service.register(req.pnr(), req.weightKg(), validKey(key));
        return ResponseEntity.status(r.replayed() ? 200 : 201).body(r);
    }

    @Op("bag_status")
    @GetMapping("/bags/{tag}")
    public BagDetail status(@PathVariable String tag) {
        return service.get(tag);
    }

    @Op("bag_event")
    @PostMapping("/bags/{tag}/events")
    public Result event(@PathVariable String tag, @Valid @RequestBody AddEvent req) {
        return service.addEvent(tag, req.eventType(), req.location());
    }

    @Op("bags_by_pnr")
    @GetMapping("/bookings/{pnr}/bags")
    public List<Bag> byPnr(@PathVariable String pnr) {
        return repo.byPnr(pnr);
    }

    @Op("flight_bags")
    @GetMapping("/flights/{flightId}/bags")
    public List<Bag> byFlight(@PathVariable long flightId, @RequestParam(defaultValue = "100") int limit) {
        return repo.byFlight(flightId, clamp(limit, 1, 500));
    }

    @Op("sample_bags")
    @GetMapping("/bags/sample")
    public List<String> sample(@RequestParam(defaultValue = "50") int limit) {
        return repo.sampleTags(clamp(limit, 1, 500));
    }

    @Op("bag_candidates")
    @GetMapping("/bags/candidates")
    public List<String> candidates(@RequestParam(defaultValue = "50") int limit) {
        return repo.candidates(clamp(limit, 1, 500));
    }

    @Op("bag_history_report")
    @GetMapping("/reports/bag-history")
    public List<HistoryRow> history(@RequestParam(required = false) Long flightId,
                                    @RequestParam(defaultValue = "30") int days) {
        return repo.history(flightId, clamp(days, 1, 365));
    }

    static String validKey(String key) {
        if (key == null || key.isBlank()) {
            return null;
        }
        if (!IDEMPOTENCY_KEY.matcher(key).matches()) {
            throw new IllegalArgumentException("Idempotency-Key 8-64 karakter [A-Za-z0-9._:-] olmalı");
        }
        return key;
    }

    static int clamp(int v, int min, int max) {
        return Math.max(min, Math.min(max, v));
    }
}

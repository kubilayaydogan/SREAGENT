package com.lab.air.checkin;

import com.lab.air.checkin.Model.Candidate;
import com.lab.air.checkin.Model.CheckinRequest;
import com.lab.air.checkin.Model.CheckinView;
import com.lab.air.checkin.Model.Result;
import com.lab.air.checkin.Model.Seat;
import com.lab.air.checkin.Model.SeatRequest;
import com.lab.air.common.Op;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
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
public class CheckinApiController {

    private final CheckinService service;
    private final CheckinRepository repo;

    public CheckinApiController(CheckinService service, CheckinRepository repo) {
        this.service = service;
        this.repo = repo;
    }

    @Op("lookup_booking")
    @GetMapping("/checkin/{pnr}")
    public CheckinView lookup(@PathVariable String pnr) {
        return service.get(pnr);
    }

    @Op("checkin")
    @PostMapping("/checkin")
    public ResponseEntity<Result> checkIn(@Valid @RequestBody CheckinRequest req) {
        Result r = service.checkIn(req.pnr(), req.ch(), 0);
        return ResponseEntity.status(r.replayed() ? 200 : 201).body(r);
    }

    @Op("assign_seat")
    @PostMapping("/checkin/{pnr}/seat")
    public Result seat(@PathVariable String pnr, @Valid @RequestBody SeatRequest req) {
        return service.assignSeat(pnr, req.seatNo());
    }

    @Op("seat_map")
    @GetMapping("/flights/{flightId}/seatmap")
    public List<Seat> seatMap(@PathVariable long flightId) {
        return repo.seatMap(flightId);
    }

    @Op("checkin_candidates")
    @GetMapping("/checkin/candidates")
    public List<Candidate> candidates(@RequestParam(required = false) Long flightId,
                                      @RequestParam(defaultValue = "false") boolean hot,
                                      @RequestParam(defaultValue = "50") int limit) {
        return repo.candidates(flightId, hot, Math.max(1, Math.min(500, limit)));
    }
}

package com.lab.air.checkin;

import com.lab.air.checkin.Model.LabHoldRequest;
import com.lab.air.checkin.Model.Result;
import com.lab.air.common.BusinessException;
import com.lab.air.common.Op;
import jakarta.validation.Valid;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Ters yönlü kilit kaynağı: Check-in gerçek check-in iş yolunda envanter satırını
 * tutar; aynı sıcak uçuşta Reservation create/cancel bekler.
 */
@RestController
@RequestMapping("/lab")
@ConditionalOnProperty(prefix = "lab", name = "enabled", havingValue = "true")
public class CheckinLabController {

    private final CheckinService service;
    private final CheckinRepository repo;

    public CheckinLabController(CheckinService service, CheckinRepository repo) {
        this.service = service;
        this.repo = repo;
    }

    @Op("lab_hold_checkin")
    @PostMapping("/checkin/hold")
    public Result hold(@Valid @RequestBody LabHoldRequest req) {
        String pnr = req.pnr();
        if (pnr == null) {
            pnr = repo.candidates(null, true, 1).stream().findFirst()
                    .orElseThrow(() -> BusinessException.conflict("NO_CANDIDATE", "sıcak uçuşta aday yok")).pnr();
        }
        return service.checkIn(pnr, "API", req.holdMs());
    }
}

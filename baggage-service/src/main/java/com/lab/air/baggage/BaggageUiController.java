package com.lab.air.baggage;

import com.lab.air.common.BusinessException;
import com.lab.air.common.DbErrors;
import com.lab.air.common.Op;
import org.springframework.dao.DataAccessException;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

import java.math.BigDecimal;
import java.util.List;
import java.util.UUID;

@Controller
public class BaggageUiController {

    private final BaggageService service;
    private final BaggageRepository repo;

    public BaggageUiController(BaggageService service, BaggageRepository repo) {
        this.service = service;
        this.repo = repo;
    }

    @Op("ui_index")
    @GetMapping("/")
    public String index(@RequestParam(required = false) String pnr, Model m) {
        m.addAttribute("idemKey", UUID.randomUUID().toString());
        m.addAttribute("pnr", pnr);
        try {
            m.addAttribute("candidates", repo.candidates(10));
            m.addAttribute("bags", pnr == null || pnr.isBlank() ? List.of() : repo.byPnr(pnr.trim()));
        } catch (DataAccessException e) {
            m.addAttribute("candidates", List.of());
            m.addAttribute("bags", List.of());
            m.addAttribute("error", describe(e));
        }
        return "index";
    }

    @GetMapping("/ui/lookup")
    public String lookup(@RequestParam String tag) {
        return "redirect:/ui/bags/" + tag.trim().toUpperCase();
    }

    @Op("ui_register_bag")
    @PostMapping("/ui/bags")
    public String register(@RequestParam String pnr, @RequestParam BigDecimal weightKg, @RequestParam String idemKey,
                           RedirectAttributes ra) {
        try {
            if (weightKg.compareTo(new BigDecimal("0.5")) < 0 || weightKg.compareTo(new BigDecimal("50")) > 0) {
                throw BusinessException.badRequest("WEIGHT_RANGE", "ağırlık 0.5..50 kg");
            }
            var r = service.register(pnr.trim(), weightKg, BaggageApiController.validKey(idemKey));
            ra.addFlashAttribute("msg", r.replayed() ? "Tekrar gönderim: mevcut bagaj" : "Bagaj kaydedildi");
            return "redirect:/ui/bags/" + r.detail().bag().tag();
        } catch (BusinessException | DataAccessException | IllegalArgumentException e) {
            ra.addFlashAttribute("error", describe(e));
            return "redirect:/?pnr=" + pnr.trim();
        }
    }

    @Op("ui_bag_status")
    @GetMapping("/ui/bags/{tag}")
    public String bag(@PathVariable String tag, Model m) {
        try {
            m.addAttribute("d", service.get(tag));
        } catch (BusinessException | DataAccessException e) {
            m.addAttribute("error", describe(e));
        }
        return "bag";
    }

    @Op("ui_bag_event")
    @PostMapping("/ui/bags/{tag}/events")
    public String event(@PathVariable String tag, @RequestParam String eventType, @RequestParam String location,
                        RedirectAttributes ra) {
        try {
            String loc = location.trim().toUpperCase();
            if (!loc.matches("[A-Z0-9-]{2,16}")) {
                throw BusinessException.badRequest("LOCATION_FORMAT", "konum ör. IST veya BELT-2");
            }
            var r = service.addEvent(tag, eventType, loc);
            ra.addFlashAttribute("msg", r.replayed() ? "Durum zaten " + eventType : "Durum: " + eventType);
        } catch (BusinessException | DataAccessException e) {
            ra.addFlashAttribute("error", describe(e));
        }
        return "redirect:/ui/bags/" + tag;
    }

    static String describe(Exception e) {
        if (e instanceof BusinessException b) {
            return b.code() + ": " + b.getMessage();
        }
        if (e instanceof DataAccessException) {
            return DbErrors.classify(e) + ": " + DbErrors.rootMessage(e);
        }
        return e.getMessage();
    }
}

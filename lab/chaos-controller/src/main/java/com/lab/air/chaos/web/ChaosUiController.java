package com.lab.air.chaos.web;

import com.lab.air.chaos.engine.ExperimentService;
import org.springframework.stereotype.Controller;
import org.springframework.ui.Model;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;

@Controller
public class ChaosUiController {

    private final ExperimentService service;

    public ChaosUiController(ExperimentService service) {
        this.service = service;
    }

    /** Yeni chaos v2 arayüzü (React SPA, /ui/). */
    @GetMapping("/")
    public String root() {
        return "redirect:/ui/";
    }

    @GetMapping({"/ui", "/ui/"})
    public String ui() {
        return "forward:/ui/index.html";
    }

    /** Klasik (v1) panel; v2 ile aynı RunGate'i paylaşır — aynı anda tek deney. */
    @GetMapping("/classic")
    public String panel() {
        return "panel";
    }

    @GetMapping("/experiments")
    public String list(Model m) {
        m.addAttribute("experiments", service.list());
        return "experiments";
    }

    /** Canlı izleme sayfası: veri tarayıcıda /api/experiments/{id} ve /api/status'tan 3 sn'de bir çekilir. */
    @GetMapping("/experiments/{id}/watch")
    public String watch(@PathVariable String id, Model m) {
        m.addAttribute("id", id);
        return "watch";
    }

    @GetMapping("/experiments/{id}")
    public String detail(@PathVariable String id, Model m) {
        service.get(id).ifPresent(r -> m.addAttribute("r", r));
        return "experiment";
    }
}

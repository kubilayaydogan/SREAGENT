package com.lab.air.chaos.ops;

import java.util.Map;

/**
 * Bir operasyonun üreteceği tek HTTP isteği.
 *
 * @param lab true ise X-Lab-Token eklenir (yalnız /lab/** uçları)
 */
public record HttpCall(String method, String path, String jsonBody, Map<String, String> headers, boolean lab) {

    public static HttpCall get(String path) {
        return new HttpCall("GET", path, null, Map.of(), false);
    }

    public static HttpCall post(String path, String json) {
        return new HttpCall("POST", path, json, Map.of(), false);
    }

    public static HttpCall labPost(String path, String json) {
        return new HttpCall("POST", path, json, Map.of(), true);
    }

    public HttpCall withHeader(String k, String v) {
        var m = new java.util.HashMap<>(headers);
        m.put(k, v);
        return new HttpCall(method, path, jsonBody, Map.copyOf(m), lab);
    }
}

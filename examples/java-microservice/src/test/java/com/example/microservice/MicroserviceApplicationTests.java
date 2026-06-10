package com.example.microservice;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@SpringBootTest
@AutoConfigureMockMvc
class MicroserviceApplicationTests {

    @Autowired
    private MockMvc mvc;

    @Test
    void healthReturnsUp() throws Exception {
        mvc.perform(get("/api/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true))
                .andExpect(jsonPath("$.data").value("UP"));
    }

    @Test
    void getUserSeedData() throws Exception {
        mvc.perform(get("/api/users/1"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.name").value("Alice"));
    }

    @Test
    void getUserNotFound() throws Exception {
        mvc.perform(get("/api/users/999"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.success").value(false));
    }

    @Test
    void createAndCancelOrder() throws Exception {
        // create
        String body = "{\"userId\":1,\"product\":\"Widget\"}";
        String response = mvc.perform(post("/api/orders")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.data.status").value("CREATED"))
                .andReturn().getResponse().getContentAsString();

        // extract id from response (JsonPath returns Integer for small numbers)
        Number orderId = com.jayway.jsonpath.JsonPath.read(response, "$.data.id");

        // cancel
        mvc.perform(delete("/api/orders/" + orderId.longValue()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.success").value(true));
    }
}

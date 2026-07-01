package com.example.microservice.tracing;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Thread-safe in-memory store for {@link SpanRecord}s keyed by trace-id.
 *
 * <p>Spans are accumulated by {@link TracingAspect} during request processing
 * and retrieved + removed by {@link TraceFilter} once the response is ready.
 * Entries older than 5 minutes are automatically purged to prevent memory leaks.
 */
@Component
public class TraceStore {

    private static final Logger log = LoggerFactory.getLogger(TraceStore.class);
    private static final long TTL_MS = 5 * 60_000L;

    private record Entry(long createdAt, List<SpanRecord> spans) {}

    private static final ConcurrentHashMap<String, Entry> store = new ConcurrentHashMap<>();

    /**
     * Append a span record to the bucket for {@code traceId}.
     * Creates the bucket if it does not yet exist.
     */
    public void add(String traceId, SpanRecord record) {
        store.computeIfAbsent(traceId, k ->
                new Entry(System.currentTimeMillis(),
                          Collections.synchronizedList(new ArrayList<>()))
        ).spans().add(record);
    }

    /**
     * Atomically removes and returns all span records accumulated for
     * {@code traceId}.  Returns an empty list if no spans were found.
     */
    public List<SpanRecord> getAndRemove(String traceId) {
        Entry entry = store.remove(traceId);
        if (entry == null) return Collections.emptyList();
        return new ArrayList<>(entry.spans());
    }

    /** Periodically remove stale entries to prevent unbounded growth. */
    @Scheduled(fixedDelay = 60_000)
    public void cleanup() {
        long cutoff = System.currentTimeMillis() - TTL_MS;
        int before = store.size();
        store.entrySet().removeIf(e -> e.getValue().createdAt() < cutoff);
        int removed = before - store.size();
        if (removed > 0) {
            log.debug("TraceStore: evicted {} stale entries", removed);
        }
    }
}

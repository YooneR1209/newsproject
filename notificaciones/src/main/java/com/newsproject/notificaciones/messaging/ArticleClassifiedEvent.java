package com.newsproject.notificaciones.messaging;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * Evento article.classified (ver docs/eventos.md).
 * Los nombres snake_case del JSON se mapean con spring.jackson.property-naming-strategy.
 */
public record ArticleClassifiedEvent(
        UUID eventId,
        String eventType,
        Instant occurredAt,
        int version,
        Payload payload) {

    public record Payload(
            UUID articleId,
            String url,
            String title,
            String summary,
            String source,
            Instant publishedAt,
            Instant ingestedAt,
            String category,
            List<String> tags,
            double score) {
    }
}

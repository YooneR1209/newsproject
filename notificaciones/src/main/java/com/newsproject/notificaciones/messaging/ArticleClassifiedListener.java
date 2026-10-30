package com.newsproject.notificaciones.messaging;

import com.newsproject.notificaciones.websocket.NotificationWebSocketHandler;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.stereotype.Component;

@Component
public class ArticleClassifiedListener {

    private static final Logger log = LoggerFactory.getLogger(ArticleClassifiedListener.class);

    private final NotificationWebSocketHandler webSocketHandler;

    public ArticleClassifiedListener(NotificationWebSocketHandler webSocketHandler) {
        this.webSocketHandler = webSocketHandler;
    }

    @RabbitListener(queues = "notificaciones.article-classified")
    public void onArticleClassified(ArticleClassifiedEvent event) {
        log.info("Procesando event_id={}", event.eventId());

        log.info("article.classified recibido: event_id={} titulo='{}' categoria={}",
                event.eventId(), event.payload().title(), event.payload().category());

        webSocketHandler.broadcast(event);
    }
}

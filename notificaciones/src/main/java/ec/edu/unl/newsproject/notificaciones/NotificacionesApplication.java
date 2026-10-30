package ec.edu.unl.newsproject.notificaciones;

import java.util.Map;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * Notificaciones — plantilla base (responsable: Ariel).
 *
 * Reemplazar por la implementación real. Consume article.classified y
 * user.preferences.updated, guarda alertas y las emite por WebSocket
 * (docs/arquitectura.md, secciones 6 y 7):
 *   GET   /api/notifications?user_id=&unread=
 *   PATCH /api/notifications/{id}/read
 *   WS    /ws?user_id=
 */
@SpringBootApplication
@RestController
public class NotificacionesApplication {

    public static void main(String[] args) {
        SpringApplication.run(NotificacionesApplication.class, args);
    }

    // TODO: responder 200 solo si PostgreSQL y RabbitMQ están conectados.
    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "ok", "service", "notificaciones");
    }
}

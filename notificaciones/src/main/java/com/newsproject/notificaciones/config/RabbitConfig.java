package com.newsproject.notificaciones.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.amqp.support.converter.Jackson2JsonMessageConverter;
import org.springframework.amqp.support.converter.MessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * Convierte los mensajes JSON de RabbitMQ a objetos Java.
 *
 * Las colas y el exchange NO se declaran aqui: los crea infra/rabbitmq/definitions.json.
 * Si Spring las declarara con argumentos distintos, RabbitMQ rechazaria la conexion
 * (PRECONDITION_FAILED).
 */
@Configuration
public class RabbitConfig {

    @Bean
    public MessageConverter jsonMessageConverter(ObjectMapper objectMapper) {
        // Reutiliza el ObjectMapper de Spring (snake_case configurado en application.yml)
        return new Jackson2JsonMessageConverter(objectMapper);
    }
}

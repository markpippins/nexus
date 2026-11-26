package org.nexus.peb.bootstrap;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.converter.AbstractJacksonHttpMessageConverter;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import java.util.List;

/**
 * Boot 4 / Spring 7 defaults MVC's JSON converter to Jackson 3
 * ({@code tools.jackson}), while the PEB domain model and Hibernate's jsonb
 * mapping use Jackson 2 {@code JsonNode}. Put a Jackson 2 converter first so
 * {@code @RequestBody}/{@code @ResponseBody} round-trips the same types the
 * persistence layer uses.
 *
 * <p>The {@link ObjectMapper} bean comes from Boot's {@code spring-boot-jackson2}
 * auto-configuration (see {@code peb-bootstrap/pom.xml}) and honours the
 * {@code spring.jackson2.*} properties in {@code application.yml}.
 */
@Configuration
public class Jackson2MvcConfiguration implements WebMvcConfigurer {

    private final ObjectMapper objectMapper;

    public Jackson2MvcConfiguration(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public void extendMessageConverters(List<HttpMessageConverter<?>> converters) {
        // Replace the Jackson 3 converter in place rather than prepending:
        // a prepended catch-all converter would also claim String responses
        // and serialize them as quoted JSON strings.
        // Drop the Jackson 3 converters (including the projecting converter
        // that sits ahead of StringHttpMessageConverter and would otherwise
        // serialize pre-rendered String bodies as quoted JSON). String and
        // byte[] handling then stays with the default converters.
        converters.removeIf(c -> c instanceof AbstractJacksonHttpMessageConverter);
        converters.add(new MappingJackson2HttpMessageConverter(objectMapper));
    }
}

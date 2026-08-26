package eg.com.efinance.powerbi.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

class SensitiveJsonSanitizerTest {

    private final SensitiveJsonSanitizer sanitizer = new SensitiveJsonSanitizer(new ObjectMapper());

    private String sanitize(String json) {
        return new String(sanitizer.sanitize(json.getBytes(StandardCharsets.UTF_8)), StandardCharsets.UTF_8);
    }

    @Test
    @DisplayName("Ownership fields are removed from objects")
    void removesOwnershipFields() {
        String result = sanitize("{\"Name\":\"Q3\",\"CreatedBy\":\"eg\\\\admin\",\"ModifiedBy\":\"eg\\\\admin\"}");

        assertThat(result).contains("\"Name\":\"Q3\"");
        assertThat(result).doesNotContain("CreatedBy").doesNotContain("ModifiedBy");
    }

    @Test
    @DisplayName("Removal reaches nested objects and arrays")
    void removesNestedFields() {
        String result = sanitize("{\"Value\":[{\"Id\":1,\"Creator\":\"a\"},{\"Id\":2,\"Owner\":\"b\"}]}");

        assertThat(result).contains("\"Id\":1").contains("\"Id\":2");
        assertThat(result).doesNotContain("Creator").doesNotContain("Owner");
    }

    @Test
    @DisplayName("Field matching ignores case")
    void matchingIsCaseInsensitive() {
        assertThat(sanitize("{\"CREATEDDATE\":\"2024-01-01\",\"keep\":1}"))
                .doesNotContain("CREATEDDATE")
                .contains("keep");
    }

    @Test
    @DisplayName("Non-JSON bodies pass through untouched")
    void leavesNonJsonAlone() {
        byte[] body = "<html><body>not json</body></html>".getBytes(StandardCharsets.UTF_8);
        assertThat(sanitizer.sanitize(body)).isEqualTo(body);
    }

    @Test
    @DisplayName("A document with nothing to remove is returned byte-identical")
    void untouchedDocumentIsNotReserialized() {
        byte[] body = "{\"Name\":\"Q3\",\"Value\":42}".getBytes(StandardCharsets.UTF_8);
        assertThat(sanitizer.sanitize(body)).isSameAs(body);
    }

    @Test
    @DisplayName("Empty and null bodies are safe")
    void handlesEmptyInput() {
        assertThat(sanitizer.sanitize(new byte[0])).isEmpty();
        assertThat(sanitizer.sanitize(null)).isNull();
    }
}

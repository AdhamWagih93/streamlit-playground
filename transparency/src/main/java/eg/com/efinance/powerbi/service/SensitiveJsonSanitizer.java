package eg.com.efinance.powerbi.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

/**
 * Strips ownership and authorship fields from proxied JSON.
 *
 * <p>A transparency report is about published figures, not about which named civil servant last
 * touched the catalog item. The Report Server returns those fields on almost every OData object, so
 * they are removed on the way out — data minimisation at the boundary rather than trusting each
 * caller to ignore them.
 */
@Component
public class SensitiveJsonSanitizer {

    private static final Logger log = LoggerFactory.getLogger(SensitiveJsonSanitizer.class);

    private static final Set<String> SENSITIVE_FIELDS = Set.of(
            "createdby",
            "modifiedby",
            "createddate",
            "modifieddate",
            "creator",
            "modifier",
            "createdbyusername",
            "modifiedbyusername",
            "owner",
            "ownerusername",
            "userid",
            "principalname");

    /** Guard against unbounded recursion on a hostile or malformed document. */
    private static final int MAX_DEPTH = 64;

    private final ObjectMapper objectMapper;

    public SensitiveJsonSanitizer(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /**
     * @return the sanitized document, or the original bytes unchanged when the payload is not
     *     parseable JSON. Failing open on parse is deliberate: a non-JSON body reaching here means
     *     the content type lied, and dropping it would break the report for no security gain.
     */
    public byte[] sanitize(byte[] body) {
        if (body == null || body.length == 0) {
            return body;
        }
        try {
            JsonNode root = objectMapper.readTree(body);
            if (root == null || root.isMissingNode()) {
                return body;
            }
            if (removeSensitive(root, 0)) {
                return objectMapper.writeValueAsBytes(root);
            }
            return body;
        } catch (Exception ex) {
            log.debug("Response declared JSON but could not be parsed; forwarding unchanged ({})", ex.toString());
            return body;
        }
    }

    /** @return true when at least one field was removed. */
    private boolean removeSensitive(JsonNode node, int depth) {
        if (depth > MAX_DEPTH) {
            return false;
        }
        boolean modified = false;

        if (node instanceof ObjectNode objectNode) {
            List<String> doomed = new ArrayList<>();
            var names = objectNode.fieldNames();
            while (names.hasNext()) {
                String name = names.next();
                if (SENSITIVE_FIELDS.contains(name.toLowerCase(Locale.ROOT))) {
                    doomed.add(name);
                }
            }
            if (!doomed.isEmpty()) {
                objectNode.remove(doomed);
                modified = true;
            }
            for (JsonNode child : objectNode) {
                modified |= removeSensitive(child, depth + 1);
            }
        } else if (node instanceof ArrayNode arrayNode) {
            for (JsonNode child : arrayNode) {
                modified |= removeSensitive(child, depth + 1);
            }
        }
        return modified;
    }
}

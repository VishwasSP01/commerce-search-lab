package lab.commerce.api;

import java.text.Normalizer;
import java.util.List;
import java.util.Set;
import tools.jackson.databind.JsonNode;

final class SearchContract {
    static final Set<String> LOCALES = Set.of("us", "es", "jp");
    record Query(String query, String locale, int limit) {}
    record Hit(String productId, double score) {}
    record Result(String locale, int limit, int returned, String retrieval, List<Hit> hits) {}

    static Query parse(JsonNode body) {
        if (body == null || !body.isObject() || body.size() < 2 || body.size() > 3) throw ApiFailure.invalid();
        for (String key : body.propertyNames()) {
            if (!Set.of("query", "locale", "limit").contains(key)) throw ApiFailure.invalid();
        }
        JsonNode query = body.get("query"), locale = body.get("locale"), limit = body.get("limit");
        if (query == null || !query.isString() || locale == null || !locale.isString()
            || !LOCALES.contains(locale.asString())) throw ApiFailure.invalid();
        String text = query.asString();
        if (text.codePointCount(0, text.length()) > 256 || text.codePoints().anyMatch(c -> c >= 0xd800 && c <= 0xdfff)) throw ApiFailure.invalid();
        text = Normalizer.normalize(text, Normalizer.Form.NFKC);
        if (text.isBlank() || text.codePointCount(0, text.length()) > 256) throw ApiFailure.invalid();
        int count = 10;
        if (limit != null) {
            if (!limit.isIntegralNumber() || !limit.canConvertToInt() || limit.intValue() < 1 || limit.intValue() > 100) throw ApiFailure.invalid();
            count = limit.intValue();
        }
        return new Query(text, locale.asString(), count);
    }
}

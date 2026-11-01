package lab.commerce.search;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;
import java.util.Map;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.similarities.BM25Similarity;
import org.apache.lucene.store.FSDirectory;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class Bm25CliTest {
  @TempDir Path temporary;
  private int next;

  private Path index(String... rows) throws Exception {
    Path products = temporary.resolve("products-" + next + ".tsv");
    Path index = temporary.resolve("index-" + next++);
    StringBuilder input = new StringBuilder();
    for (String row : rows) {
      String[] parts = row.split("\\|", 3);
      input.append(parts[0]).append('\t').append(parts[1]).append('\t')
          .append(Base64.getEncoder().encodeToString(parts[2].getBytes(StandardCharsets.UTF_8))).append('\n');
    }
    Files.writeString(products, input);
    assertEquals(rows.length, Bm25Cli.index(products, index, 1.2f, .75f));
    return index;
  }

  private Bm25Cli.Scores scores(Path index, String locale, String query, String... ids) throws Exception {
    try (var directory = FSDirectory.open(index); var reader = DirectoryReader.open(directory);
         var analyzer = Bm25Cli.analyzer()) {
      var searcher = new IndexSearcher(reader);
      searcher.setSimilarity(new BM25Similarity(1.2f, .75f));
      return Bm25Cli.score(searcher, analyzer, locale, query, List.of(ids));
    }
  }

  @Test void multilingualAnalysisMatchesInflectionsAndUnspacedJapanese() throws Exception {
    Path index = index("us|A|Running shoes", "es|A|Zapatos rojos", "jp|A|赤いランニングシューズ");
    assertTrue(scores(index, "us", "shoe", "A").values().get("A") > 0);
    assertTrue(scores(index, "es", "zapato", "A").values().get("A") > 0);
    assertTrue(scores(index, "jp", "ランニングシューズ", "A").values().get("A") > 0);
    try (var analyzer = Bm25Cli.analyzer()) {
      assertTrue(Bm25Cli.tokens(analyzer, "jp", "赤いランニングシューズ").size() > 1);
    }
  }

  @Test void identicalIdsRemainLocaleScoped() throws Exception {
    Path index = index("us|A|red shoe", "es|A|blue hat");
    assertTrue(scores(index, "us", "red", "A").values().get("A") > 0);
    assertEquals(0f, scores(index, "es", "red", "A").values().get("A"));
  }

  @Test void zeroMatchesAndStopwordOnlyQueriesRetainAllCandidates() throws Exception {
    Path index = index("us|A|red shoe", "us|B|blue hat");
    assertEquals(Map.of("A", 0f, "B", 0f), scores(index, "us", "unknownword", "A", "B").values());
    var stopped = scores(index, "us", "the and", "A", "B");
    assertEquals(0, stopped.analyzedTerms());
    assertEquals(Map.of("A", 0f, "B", 0f), stopped.values());
  }

  @Test void querySyntaxIsLiteralAnalyzedText() throws Exception {
    Path index = index("us|A|red shoe", "us|B|blue hat");
    assertTrue(scores(index, "us", "shoe OR (red", "A", "B").values().get("A") > 0);
  }

  @Test void missingAndDuplicateCandidatesFailInsteadOfBecomingZeroScores() throws Exception {
    Path index = index("us|A|shoe");
    assertThrows(IllegalArgumentException.class, () -> scores(index, "us", "shoe", "missing"));
    assertThrows(IllegalArgumentException.class, () -> scores(index, "us", "shoe", "A", "A"));
  }

  @Test void lengthNormalizationFavorsShorterDocumentForSameTermFrequency() throws Exception {
    Path index = index("us|A|shoe", "us|B|shoe colorful running footwear accessory");
    var values = scores(index, "us", "shoe", "A", "B").values();
    assertTrue(values.get("A") > values.get("B"));
  }

  @Test void collectionStatisticsAreGlobalWithinLocaleAndIndependentOfCandidateFilter() throws Exception {
    Path base = index("us|A|red shoe", "us|B|blue hat");
    float original = scores(base, "us", "red", "A", "B").values().get("A");
    assertEquals(original, scores(base, "us", "red", "A").values().get("A"));
    Path otherLocale = index("us|A|red shoe", "us|B|blue hat", "es|C|red shoe");
    assertEquals(original, scores(otherLocale, "us", "red", "A", "B").values().get("A"));
    Path sameLocale = index("us|A|red shoe", "us|B|blue hat", "us|C|red shoe");
    assertNotEquals(original, scores(sameLocale, "us", "red", "A", "B").values().get("A"));
  }

  @Test void fileProtocolPreservesNewlinesUnicodeAndEveryCandidate() throws Exception {
    Path index = index("jp|A|赤い\nランニングシューズ", "jp|B|青い帽子");
    Path queries = temporary.resolve("queries.tsv");
    String encoded = Base64.getEncoder().encodeToString("ランニングシューズ".getBytes(StandardCharsets.UTF_8));
    Files.writeString(queries, "jp\t123\t" + encoded + "\tA,B\n");
    Path output = temporary.resolve("scores.tsv");
    assertEquals(1, Bm25Cli.scoreFile(index, queries, output, 1.2f, .75f));
    List<String> rows = Files.readAllLines(output);
    assertEquals(3, rows.size());
    assertTrue(rows.get(1).startsWith("jp\t123\tA\t"));
    assertTrue(rows.get(2).startsWith("jp\t123\tB\t0.0\t"));
  }

  @Test void existingIndexCannotBeOverwritten() throws Exception {
    Path index = index("us|A|shoe");
    assertThrows(IllegalArgumentException.class,
        () -> Bm25Cli.index(temporary.resolve("unused"), index, 1.2f, .75f));
  }
}

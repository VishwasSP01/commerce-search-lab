package lab.commerce.search;

import static org.junit.jupiter.api.Assertions.*;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.List;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.similarities.BM25Similarity;
import org.apache.lucene.store.FSDirectory;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class CatalogCliTest {
  @TempDir Path temporary;

  private Path products(String... rows) throws Exception {
    Path file = temporary.resolve("products.tsv");
    var text = new StringBuilder();
    for (String row : rows) {
      String[] parts = row.split("\\|", 3);
      text.append(parts[0]).append('\t').append(parts[1]).append('\t')
          .append(Base64.getEncoder().encodeToString(parts[2].getBytes(StandardCharsets.UTF_8))).append('\n');
    }
    Files.writeString(file, text);
    return file;
  }

  private CatalogCli.Retrieval retrieve(Path index, String locale, String query, int cutoff) throws Exception {
    try (var directory = FSDirectory.open(index); var reader = DirectoryReader.open(directory);
         var analyzer = Bm25Cli.analyzer()) {
      var searcher = new IndexSearcher(reader);
      searcher.setSimilarity(new BM25Similarity(1.2f, .75f));
      return CatalogCli.retrieve(searcher, analyzer, locale, query, cutoff);
    }
  }

  @Test void cutoffTiesUseProductIdRatherThanInsertionOrderAndStayInLocale() throws Exception {
    Path index = temporary.resolve("index");
    CatalogCli.index(products("us|Z|shoe", "es|A|shoe", "us|M|shoe", "us|A|shoe"), index, 1.2f, .75f);
    var hits = retrieve(index, "us", "shoe", 2).hits();
    assertEquals(List.of("A", "M"), hits.stream().map(CatalogCli.Hit::productId).toList());
    assertEquals(hits.get(0).score(), hits.get(1).score());
    assertEquals(List.of("A"), retrieve(index, "es", "shoe", 100).hits().stream().map(CatalogCli.Hit::productId).toList());
  }

  @Test void addedMetadataLeavesBaselineCandidateScoresUnchanged() throws Exception {
    Path input = products("us|A|running shoes", "us|B|shoe with red sole", "us|C|blue hat",
                          "jp|A|赤いシューズ", "es|A|zapatos rojos");
    Path old = temporary.resolve("old"), catalog = temporary.resolve("catalog");
    Bm25Cli.index(input, old, 1.2f, .75f);
    CatalogCli.index(input, catalog, 1.2f, .75f);
    try (var oldDirectory = FSDirectory.open(old); var newDirectory = FSDirectory.open(catalog);
         var oldReader = DirectoryReader.open(oldDirectory); var newReader = DirectoryReader.open(newDirectory);
         var analyzer = Bm25Cli.analyzer()) {
      var before = new IndexSearcher(oldReader);
      var after = new IndexSearcher(newReader);
      before.setSimilarity(new BM25Similarity(1.2f, .75f));
      after.setSimilarity(new BM25Similarity(1.2f, .75f));
      var expected = Bm25Cli.score(before, analyzer, "us", "shoe", List.of("A", "B", "C"));
      assertEquals(expected, Bm25Cli.score(after, analyzer, "us", "shoe", List.of("A", "B", "C")));
      for (var hit : CatalogCli.retrieve(after, analyzer, "us", "shoe", 100).hits()) {
        assertEquals(expected.values().get(hit.productId()).floatValue(), hit.score());
      }
    }
  }

  @Test void shortAndEmptyListsAreNeverPadded() throws Exception {
    Path index = temporary.resolve("index");
    CatalogCli.index(products("us|A|shoe", "us|B|hat", "es|A|zapato"), index, 1.2f, .75f);
    assertEquals(1, retrieve(index, "us", "shoe", 100).hits().size());
    assertTrue(retrieve(index, "us", "unfindablexyz", 100).hits().isEmpty());
    var empty = retrieve(index, "es", "no como todos", 100);
    assertEquals(0, empty.analyzedTerms());
    assertTrue(empty.hits().isEmpty());
    assertThrows(IllegalArgumentException.class, () -> retrieve(index, "de", "shoe", 100));
    assertThrows(IllegalArgumentException.class, () -> retrieve(index, "us", "shoe", 0));
  }

  @Test void overwriteAndDuplicateCatalogIdentitiesFail() throws Exception {
    Path input = products("us|A|shoe");
    Path index = temporary.resolve("index");
    CatalogCli.index(input, index, 1.2f, .75f);
    assertThrows(IllegalArgumentException.class, () -> CatalogCli.index(input, index, 1.2f, .75f));
    Path duplicate = products("us|A|shoe", "us|A|hat");
    assertThrows(IllegalArgumentException.class,
        () -> CatalogCli.index(duplicate, temporary.resolve("duplicates"), 1.2f, .75f));
  }
}

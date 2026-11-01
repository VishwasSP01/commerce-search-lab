package lab.commerce.search;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.SortedDocValuesField;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.index.Term;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.Sort;
import org.apache.lucene.search.SortField;
import org.apache.lucene.search.TermQuery;
import org.apache.lucene.search.similarities.BM25Similarity;
import org.apache.lucene.store.FSDirectory;
import org.apache.lucene.util.BytesRef;
import org.apache.lucene.util.QueryBuilder;
import org.apache.lucene.util.Version;

/** Full-catalog lexical retrieval. Its query protocol contains no labels or candidate IDs. */
public final class CatalogCli {
  private static final Sort ORDER = new Sort(
      SortField.FIELD_SCORE, new SortField("product_id_sort", SortField.Type.STRING));

  /** Same title fields as the frozen baseline, plus explicit locale and deterministic ID sorting. */
  static long index(Path products, Path index, float k1, float b) throws IOException {
    if (Files.exists(index)) throw new IllegalArgumentException("Index path already exists: " + index);
    long count = 0;
    var keys = new HashSet<String>();
    try (Analyzer analyzer = Bm25Cli.analyzer(); var directory = FSDirectory.open(index)) {
      var config = new IndexWriterConfig(analyzer)
          .setOpenMode(IndexWriterConfig.OpenMode.CREATE)
          .setSimilarity(new BM25Similarity(k1, b)).setRAMBufferSizeMB(128);
      try (var writer = new IndexWriter(directory, config);
           var input = Files.newBufferedReader(products, StandardCharsets.UTF_8)) {
        String line;
        while ((line = input.readLine()) != null) {
          String[] parts = line.split("\t", -1);
          if (parts.length != 3 || parts[1].isEmpty() || parts[1].contains(",")) {
            throw new IllegalArgumentException("Malformed product row");
          }
          String field = Bm25Cli.field(parts[0]);
          String key = parts[0] + ":" + parts[1];
          if (!keys.add(key)) throw new IllegalArgumentException("Duplicated locale-product key");
          var doc = new Document();
          doc.add(new StringField("key", key, Field.Store.NO));
          doc.add(new StringField("locale", parts[0], Field.Store.YES));
          doc.add(new StoredField("product_id", parts[1]));
          doc.add(new SortedDocValuesField("product_id_sort", new BytesRef(parts[1])));
          doc.add(new TextField(field, Bm25Cli.normalize(Bm25Cli.decode(parts[2])), Field.Store.NO));
          writer.addDocument(doc);
          count++;
          if (count % 250_000 == 0) System.err.println("Indexed " + count + " catalog products");
        }
      }
    }
    return count;
  }

  record Hit(String productId, float score) {}
  record Retrieval(List<Hit> hits, int analyzedTerms) {}

  static Retrieval retrieve(IndexSearcher searcher, Analyzer analyzer, String locale,
                            String text, int cutoff) throws IOException {
    if (cutoff < 1) throw new IllegalArgumentException("Cutoff must be positive");
    String field = Bm25Cli.field(locale);
    int terms = Bm25Cli.tokens(analyzer, locale, text).size();
    var lexical = new QueryBuilder(analyzer).createBooleanQuery(field, Bm25Cli.normalize(text));
    List<Hit> hits = new ArrayList<>();
    if (lexical != null) {
      var query = new BooleanQuery.Builder()
          .add(new TermQuery(new Term("locale", locale)), BooleanClause.Occur.FILTER)
          .add(lexical, BooleanClause.Occur.MUST).build();
      var stored = searcher.storedFields();
      for (var hit : searcher.search(query, cutoff, ORDER, true).scoreDocs) {
        if (!Float.isFinite(hit.score) || hit.score < 0) throw new IllegalStateException("Invalid score");
        var doc = stored.document(hit.doc);
        if (!locale.equals(doc.get("locale"))) throw new IllegalStateException("Cross-locale result");
        hits.add(new Hit(doc.get("product_id"), hit.score));
      }
    }
    return new Retrieval(hits, terms);
  }

  /** Input: locale, query ID, Base64 query. Results and query status have separate TSV headers. */
  static long retrieveFile(Path index, Path queries, Path results, Path status,
                           int cutoff, float k1, float b) throws IOException {
    long count = 0;
    var seen = new HashSet<String>();
    try (Analyzer analyzer = Bm25Cli.analyzer(); var directory = FSDirectory.open(index);
         var reader = DirectoryReader.open(directory);
         var input = Files.newBufferedReader(queries, StandardCharsets.UTF_8);
         var output = Files.newBufferedWriter(results, StandardCharsets.UTF_8);
         var states = Files.newBufferedWriter(status, StandardCharsets.UTF_8)) {
      var searcher = new IndexSearcher(reader);
      searcher.setSimilarity(new BM25Similarity(k1, b));
      output.write("product_locale\tquery_id\trank\tproduct_id\tscore\n");
      states.write("product_locale\tquery_id\tanalyzed_terms\treturned\n");
      String line;
      while ((line = input.readLine()) != null) {
        String[] parts = line.split("\t", -1);
        if (parts.length != 3) throw new IllegalArgumentException("Malformed retrieval query row");
        Long.parseLong(parts[1]);
        if (!seen.add(parts[0] + ":" + parts[1])) throw new IllegalArgumentException("Duplicated query");
        var retrieval = retrieve(searcher, analyzer, parts[0], Bm25Cli.decode(parts[2]), cutoff);
        int rank = 0;
        for (Hit hit : retrieval.hits()) {
          output.write(parts[0] + "\t" + parts[1] + "\t" + (++rank) + "\t" + hit.productId()
              + "\t" + hit.score() + "\n");
        }
        states.write(parts[0] + "\t" + parts[1] + "\t" + retrieval.analyzedTerms()
            + "\t" + retrieval.hits().size() + "\n");
        count++;
      }
    }
    return count;
  }

  public static void main(String[] args) throws IOException {
    if (args.length == 5 && args[0].equals("index")) {
      System.out.println("products=" + index(Path.of(args[1]), Path.of(args[2]),
          Float.parseFloat(args[3]), Float.parseFloat(args[4])));
    } else if (args.length == 8 && args[0].equals("retrieve")) {
      System.out.println("queries=" + retrieveFile(Path.of(args[1]), Path.of(args[2]), Path.of(args[3]),
          Path.of(args[4]), Integer.parseInt(args[5]), Float.parseFloat(args[6]), Float.parseFloat(args[7])));
    } else {
      throw new IllegalArgumentException("Usage: index products.tsv index-dir k1 b | retrieve index-dir queries.tsv results.tsv status.tsv cutoff k1 b");
    }
    System.out.println("lucene=" + Version.LATEST);
  }
}

package lab.commerce.search;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.text.Normalizer;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.apache.lucene.analysis.Analyzer;
import org.apache.lucene.analysis.TokenStream;
import org.apache.lucene.analysis.en.EnglishAnalyzer;
import org.apache.lucene.analysis.es.SpanishAnalyzer;
import org.apache.lucene.analysis.ja.JapaneseAnalyzer;
import org.apache.lucene.analysis.miscellaneous.PerFieldAnalyzerWrapper;
import org.apache.lucene.analysis.standard.StandardAnalyzer;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.search.BooleanClause;
import org.apache.lucene.search.BooleanQuery;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.TermInSetQuery;
import org.apache.lucene.search.similarities.BM25Similarity;
import org.apache.lucene.store.FSDirectory;
import org.apache.lucene.util.BytesRef;
import org.apache.lucene.util.QueryBuilder;
import org.apache.lucene.util.Version;

/** Offline title-only ranking. Labels never enter this Java process. */
public final class Bm25Cli {
  private static final Set<String> LOCALES = Set.of("us", "es", "jp");

  static String field(String locale) {
    if (!LOCALES.contains(locale)) throw new IllegalArgumentException("Unsupported locale: " + locale);
    return "title_" + locale;
  }

  static Analyzer analyzer() {
    return new PerFieldAnalyzerWrapper(new StandardAnalyzer(), Map.of(
        "title_us", new EnglishAnalyzer(), "title_es", new SpanishAnalyzer(),
        "title_jp", new JapaneseAnalyzer()));
  }

  static String normalize(String text) {
    return Normalizer.normalize(text, Normalizer.Form.NFKC);
  }

  static List<String> tokens(Analyzer analyzer, String locale, String text) throws IOException {
    List<String> result = new ArrayList<>();
    try (TokenStream stream = analyzer.tokenStream(field(locale), normalize(text))) {
      CharTermAttribute term = stream.addAttribute(CharTermAttribute.class);
      stream.reset();
      while (stream.incrementToken()) result.add(term.toString());
      stream.end();
    }
    return result;
  }

  static String decode(String encoded) {
    return new String(Base64.getDecoder().decode(encoded), StandardCharsets.UTF_8);
  }

  /** Products TSV: locale, product ID, UTF-8 title encoded as Base64. No header. */
  static long index(Path products, Path index, float k1, float b) throws IOException {
    if (Files.exists(index)) throw new IllegalArgumentException("Index path already exists: " + index);
    long count = 0;
    try (Analyzer analyzer = analyzer(); var directory = FSDirectory.open(index)) {
      var config = new IndexWriterConfig(analyzer)
          .setOpenMode(IndexWriterConfig.OpenMode.CREATE)
          .setSimilarity(new BM25Similarity(k1, b)).setRAMBufferSizeMB(128);
      try (var writer = new IndexWriter(directory, config);
           var input = Files.newBufferedReader(products, StandardCharsets.UTF_8)) {
        String line;
        while ((line = input.readLine()) != null) {
          String[] parts = line.split("\t", -1);
          if (parts.length != 3 || parts[1].isEmpty()) throw new IllegalArgumentException("Malformed product row");
          var doc = new Document();
          doc.add(new StringField("key", parts[0] + ":" + parts[1], Field.Store.NO));
          doc.add(new StoredField("product_id", parts[1]));
          doc.add(new TextField(field(parts[0]), normalize(decode(parts[2])), Field.Store.NO));
          writer.addDocument(doc);
          count++;
          if (count % 250_000 == 0) System.err.println("Indexed " + count + " products");
        }
      }
    }
    return count;
  }

  record Scores(Map<String, Float> values, int analyzedTerms) {}

  static Scores score(IndexSearcher searcher, Analyzer analyzer, String locale,
                      String text, List<String> candidates) throws IOException {
    if (candidates.isEmpty() || new HashSet<>(candidates).size() != candidates.size()) {
      throw new IllegalArgumentException("Candidates must be nonempty and unique");
    }
    List<BytesRef> keys = candidates.stream().map(id -> new BytesRef(locale + ":" + id)).toList();
    Query filter = new TermInSetQuery("key", keys);
    var coverage = searcher.search(filter, candidates.size());
    if (coverage.totalHits.value() != candidates.size()) {
      throw new IllegalArgumentException("A candidate is missing or duplicated in the index");
    }
    var stored = searcher.storedFields();
    Set<String> indexed = new HashSet<>();
    for (var hit : coverage.scoreDocs) indexed.add(stored.document(hit.doc).get("product_id"));
    if (!indexed.equals(new HashSet<>(candidates))) throw new IllegalArgumentException("Candidate identity mismatch");
    Map<String, Float> result = new HashMap<>();
    for (String id : candidates) result.put(id, 0f);
    String normalized = normalize(text);
    int terms = tokens(analyzer, locale, text).size();
    Query lexical = new QueryBuilder(analyzer).createBooleanQuery(field(locale), normalized);
    if (lexical != null) {
      Query query = new BooleanQuery.Builder()
          .add(filter, BooleanClause.Occur.FILTER)
          .add(lexical, BooleanClause.Occur.MUST).build();
      for (var hit : searcher.search(query, candidates.size()).scoreDocs) {
        if (!Float.isFinite(hit.score) || hit.score < 0) throw new IllegalStateException("Invalid score");
        result.put(stored.document(hit.doc).get("product_id"), hit.score);
      }
    }
    return new Scores(result, terms);
  }

  /** Queries TSV: locale, query ID, Base64 UTF-8 query, comma-separated candidate IDs. */
  static long scoreFile(Path index, Path queries, Path output, float k1, float b) throws IOException {
    long count = 0;
    try (Analyzer analyzer = analyzer(); var directory = FSDirectory.open(index);
         var reader = DirectoryReader.open(directory);
         var input = Files.newBufferedReader(queries, StandardCharsets.UTF_8);
         var writer = Files.newBufferedWriter(output, StandardCharsets.UTF_8)) {
      var searcher = new IndexSearcher(reader);
      searcher.setSimilarity(new BM25Similarity(k1, b));
      writer.write("product_locale\tquery_id\tproduct_id\tscore\tanalyzed_terms\n");
      String line;
      while ((line = input.readLine()) != null) {
        String[] parts = line.split("\t", -1);
        if (parts.length != 4) throw new IllegalArgumentException("Malformed query row");
        Long.parseLong(parts[1]);
        var candidates = Arrays.asList(parts[3].split(",", -1));
        Scores scores = score(searcher, analyzer, parts[0], decode(parts[2]), candidates);
        for (String id : candidates) {
          writer.write(parts[0] + "\t" + parts[1] + "\t" + id + "\t" + scores.values().get(id)
              + "\t" + scores.analyzedTerms() + "\n");
        }
        count++;
      }
    }
    return count;
  }

  public static void main(String[] args) throws IOException {
    if (args.length == 5 && args[0].equals("index")) {
      long products = index(Path.of(args[1]), Path.of(args[2]), Float.parseFloat(args[3]), Float.parseFloat(args[4]));
      System.out.println("products=" + products);
      System.out.println("lucene=" + Version.LATEST);
    } else if (args.length == 6 && args[0].equals("score")) {
      long queries = scoreFile(Path.of(args[1]), Path.of(args[2]), Path.of(args[3]),
          Float.parseFloat(args[4]), Float.parseFloat(args[5]));
      System.out.println("queries=" + queries);
    } else {
      throw new IllegalArgumentException("Usage: index products.tsv index-dir k1 b | score index-dir queries.tsv scores.tsv k1 b");
    }
  }
}

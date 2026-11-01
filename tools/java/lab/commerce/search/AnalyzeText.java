package lab.commerce.search;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Base64;
import java.util.stream.Collectors;
import org.apache.lucene.util.Version;

/** Diagnostic bridge: calls the unchanged ranker's exact tokenization methods. */
public final class AnalyzeText {
  public static void main(String[] args) throws Exception {
    if (args.length != 2) throw new IllegalArgumentException("Usage: AnalyzeText input.tsv output.tsv");
    long count = 0;
    try (var analyzer = Bm25Cli.analyzer();
         var input = Files.newBufferedReader(Path.of(args[0]), StandardCharsets.UTF_8);
         var output = Files.newBufferedWriter(Path.of(args[1]), StandardCharsets.UTF_8)) {
      String line;
      while ((line = input.readLine()) != null) {
        String[] parts = line.split("\t", -1);
        if (parts.length != 3) throw new IllegalArgumentException("Malformed diagnostic record");
        Long.parseLong(parts[0]);
        String text = new String(Base64.getDecoder().decode(parts[2]), StandardCharsets.UTF_8);
        var tokens = Bm25Cli.tokens(analyzer, parts[1], text);
        String encoded = tokens.stream().map(token -> Base64.getEncoder()
            .encodeToString(token.getBytes(StandardCharsets.UTF_8))).collect(Collectors.joining(","));
        output.write(parts[0] + "\t" + tokens.size() + "\t" + encoded + "\n");
        count++;
      }
    }
    System.out.println("records=" + count);
    System.out.println("lucene=" + Version.LATEST);
  }
}

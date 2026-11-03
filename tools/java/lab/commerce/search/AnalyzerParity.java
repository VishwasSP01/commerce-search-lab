package lab.commerce.search;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashSet;
import org.apache.lucene.analysis.tokenattributes.CharTermAttribute;
import org.apache.lucene.analysis.tokenattributes.OffsetAttribute;
import org.apache.lucene.analysis.tokenattributes.PositionIncrementAttribute;
import org.apache.lucene.analysis.tokenattributes.PositionLengthAttribute;
import org.apache.lucene.util.Version;

/** Probe the unchanged analyzer, including graph positions and UTF-16 offsets. */
public final class AnalyzerParity {
  public static void main(String[] args) throws Exception {
    if (args.length != 2) throw new IllegalArgumentException("Expected probes.tsv output.tsv");
    var seen = new HashSet<String>();
    try (var analyzer = Bm25Cli.analyzer();
         var in = Files.newBufferedReader(Path.of(args[0]), StandardCharsets.UTF_8);
         var out = Files.newBufferedWriter(Path.of(args[1]), StandardCharsets.UTF_8)) {
      String line;
      while ((line = in.readLine()) != null) {
        var parts = line.split("\t", -1);
        if (parts.length != 3 || !seen.add(parts[1])) throw new IllegalArgumentException("Invalid probe");
        var values = new ArrayList<String>();
        try (var stream = analyzer.tokenStream(Bm25Cli.field(parts[0]), Bm25Cli.normalize(Bm25Cli.decode(parts[2])))) {
          var term = stream.addAttribute(CharTermAttribute.class);
          var inc = stream.addAttribute(PositionIncrementAttribute.class);
          var length = stream.addAttribute(PositionLengthAttribute.class);
          var offset = stream.addAttribute(OffsetAttribute.class);
          int position = -1;
          stream.reset();
          while (stream.incrementToken()) {
            position += inc.getPositionIncrement();
            String token = Base64.getEncoder().encodeToString(term.toString().getBytes(StandardCharsets.UTF_8));
            values.add(token + ":" + position + ":" + length.getPositionLength()
                + ":" + offset.startOffset() + ":" + offset.endOffset());
          }
          stream.end();
        }
        out.write(parts[1] + "\t" + String.join(",", values) + "\n");
      }
    }
    System.out.println("lucene=" + Version.LATEST);
    System.out.println("probes=" + seen.size());
  }
}

ARG ES_BASE=docker.elastic.co/elasticsearch/elasticsearch:9.5.6@sha256:79b4f1de4c15e6eca824723086a51e5934406d614d410cc11be77748cafac537
FROM ${ES_BASE}
ADD --checksum=sha256:e4dc376879c017ad571f70c170b40fc7ae60883a9da14e6e8972e9403c10e633 --chown=elasticsearch:root https://artifacts.elastic.co/downloads/elasticsearch-plugins/analysis-kuromoji/analysis-kuromoji-9.5.6.zip /tmp/analysis-kuromoji.zip
RUN bin/elasticsearch-plugin install --batch file:///tmp/analysis-kuromoji.zip && rm /tmp/analysis-kuromoji.zip

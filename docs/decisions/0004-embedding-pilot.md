# Decision 0004: bounded multilingual embedding feasibility

Status: predeclared before encoding.

Full-catalog BM25 now has a reproducible incomplete-judgment evaluation contract. Before committing to 1.8 million embeddings, measure whether a compact multilingual encoder works on the existing zero-budget machine.

Use `intfloat/multilingual-e5-small` at the exact revision in the source manifest, downloading only enumerated files and verifying sizes and upstream SHA-256 or Git-blob hashes. Load local safetensors with remote code disabled and network access disabled during inference. The MIT-licensed model uses 384 dimensions; follow its documented query/passage prefixes, masked mean pooling and L2 normalization. Keep a separate locked environment to preserve the frozen BM25 dependency/source fingerprints. Linux CI uses CPU-only PyTorch wheels and tiny fabricated model fixtures, not public model downloads.

Select 1,024 metadata-only titles per locale by the fixed hash policy, without opening any query or judgment partition. Preserve locale-product identity and source/sample hashes. Normalize titles with NFKC and prefix `passage: `. Use float32, eager attention, batch size 32 and max length 128, a deliberate title-workload limit rather than the model's 512-token maximum. Measure untruncated token lengths and truncation per locale; do not hide overflow. There is no batch-size, sequence-length or model sweep.

Prefer MPS if available, recording the actual backend, and use CPU otherwise. Disable MPS unsupported-operator fallback. Warm one batch per locale outside stage timing. Synchronize GPU work before finishing timed stages. Record per-locale compute-stage throughput, raw vector bytes, end-to-end worker time and model-load overhead. Report kernel process maximum RSS and separate observed MPS tensor/driver allocations after forward calls; neither establishes total system peak or strict GPU peak, and they must not be added together on unified memory.

Checkpoint every 256 titles with atomic file/manifest replacement and checksums. Bind the run to the sample, model files, settings, source and runtime. Reject changed contracts or corrupted committed payloads. A final manifest commits a shard; an orphan payload without its manifest entry is recomputed. Lock the output against concurrent writers. This protocol targets process interruption, not power-loss durability or multi-host exactly-once semantics.

Run a clean pilot. Then terminate another real model worker with SIGKILL after two committed shards, restart it, require the existing committed hashes to remain unchanged and compare every resumed vector with the clean run (max absolute difference <=1e-5, minimum cosine >=0.99999). Unit fixtures also exercise incomplete shard writes and corruption. Do not require cross-device or cross-version byte equality.

Project full-catalog compute time with **locale-weighted** seconds per title, not the equally sampled aggregate rate. Show a range using observed slowest/fastest shard rates as a descriptive planning bound, not a confidence interval. Download, startup, index build, sustained thermal effects and host contention remain outside this compute-only projection. Raw float32 storage is arithmetic; ANN overhead and retrieval capacity remain unmeasured.

Completion requires finite, correctly shaped unit vectors for all three locales, successful restart/identity checks, tested pooling/prefix/truncation behavior, measurements and explicit limitations, then focused commits and a PR. A slow or CPU-only outcome is still useful evidence. No relevance improvement, training, full-catalog embedding run or dense/fusion retrieval is included.

References: [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small), [PyTorch 2.9 MPS](https://docs.pytorch.org/docs/2.9/notes/mps.html), [MPS synchronization and memory APIs](https://docs.pytorch.org/docs/2.9/mps.html).

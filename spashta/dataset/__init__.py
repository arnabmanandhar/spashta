"""
Building and measuring against a real, frozen corpus.

  snapshot   freeze the store into a hashed, reproducible corpus snapshot
  release    the redistributable part (URLs, hashes, labels, offsets) and re-fetching the text
  topics     coarse topic tags for stratified sampling
  sample     stratified article sample and candidate pairs for corroboration labels
  labels     the label format, gold labels from annotators
  agreement  Cohen's kappa per signal, with bootstrap intervals
  qrels      pooled relevance judgments for retrieval queries
  labeltool  a self-contained HTML page for labelling articles, pairs and query results
"""
